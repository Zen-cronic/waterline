"""Deterministic safety gate between the semantic Verifier and FollowingAgent.

The LLM verifier is useful for semantic review, but an irreversible action must
not depend on a free-form sentence alone.  This module fail-closes unless both
the LLM verdict and a small set of machine-checkable provenance invariants pass.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from .emit import emit_panel, emit_step

_NOTAM_INDEX = re.compile(r"(?:\bidx\s*[:#]?\s*|\bNOTAM\s+#?)(\d+)\b", re.IGNORECASE)
_APPROVED = re.compile(r"^APPROVED(?:\s|—|-|$)", re.IGNORECASE)


@dataclass(frozen=True)
class VerificationDecision:
    approved: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_dispatch_readiness(state: Mapping[str, Any]) -> VerificationDecision:
    """Fail closed unless briefing proof and authenticated attestation agree."""
    briefing_decision = assess_briefing_readiness(state)
    reasons = list(briefing_decision.reasons)
    owner_ref = state.get("mission_owner_ref")
    mission_id = state.get("mission_id")
    attestation = state.get("pilot_attestation")

    if not isinstance(owner_ref, str) or not owner_ref:
        reasons.append("authenticated mission owner is missing")
    if not isinstance(mission_id, str) or not mission_id:
        reasons.append("server-owned mission id is missing")
    if not isinstance(attestation, Mapping) or attestation.get("confirmed") is not True:
        reasons.append("authenticated pilot attestation is missing")
    elif attestation.get("actor_ref") != owner_ref or attestation.get("mission_id") != mission_id:
        reasons.append("pilot attestation is not bound to this mission owner")

    return VerificationDecision(not reasons, tuple(reasons))


def assess_briefing_readiness(state: Mapping[str, Any]) -> VerificationDecision:
    """Validate semantic approval and reproducible briefing provenance."""
    reasons: list[str] = []
    verdict = state.get("verification")
    briefing = state.get("briefing")
    weather = state.get("weather")
    corridor = state.get("corridor")

    if not isinstance(verdict, str) or not _APPROVED.match(verdict.strip()):
        reasons.append("semantic verifier did not approve")
    if not isinstance(briefing, str) or not briefing.strip():
        reasons.append("briefing is missing")
    if not isinstance(weather, Mapping) or not weather.get("available"):
        reasons.append("weather inference is unavailable")
    if not isinstance(corridor, Mapping) or not isinstance(corridor.get("hazards"), list):
        reasons.append("corridor provenance is unavailable")

    if reasons:
        return VerificationDecision(False, tuple(reasons))

    assert isinstance(briefing, str)
    assert isinstance(weather, Mapping)
    assert isinstance(corridor, Mapping)

    upper_briefing = briefing.upper()
    if "INFERRED" not in upper_briefing:
        reasons.append("weather is not explicitly labelled INFERRED")
    if not briefing.rstrip().endswith("PILOT REVIEW REQUIRED."):
        reasons.append("pilot-review boundary is missing")

    sources = weather.get("sources")
    if not isinstance(sources, list) or not sources:
        reasons.append("weather source records are missing")
    else:
        primary = sources[0] if isinstance(sources[0], Mapping) else {}
        station_id = primary.get("station_id")
        if not isinstance(station_id, str) or station_id.upper() not in upper_briefing:
            reasons.append("primary source station is not cited in the briefing")

    reach_nm = weather.get("reach_nm")
    if not isinstance(reach_nm, (int, float)):
        reasons.append("nearest-station distance is missing")
    else:
        distance_tokens = {str(reach_nm), f"{float(reach_nm):g}"}
        if not any(token in briefing for token in distance_tokens):
            reasons.append("nearest-station distance is not cited in the briefing")

    hazards = corridor.get("hazards", [])
    allowed_indices = {
        hazard.get("idx") for hazard in hazards
        if isinstance(hazard, Mapping) and isinstance(hazard.get("idx"), int)
    }
    referenced_indices = {int(match) for match in _NOTAM_INDEX.findall(briefing)}
    unknown_indices = sorted(referenced_indices - allowed_indices)
    if unknown_indices:
        reasons.append(f"unknown NOTAM indices referenced: {unknown_indices}")

    condition_receipt = state.get("condition_receipt")
    condition_evidence = state.get("condition_evidence")
    flight_plan = state.get("flight_plan")
    if condition_receipt is not None:
        if not isinstance(condition_receipt, Mapping):
            reasons.append("condition-card model receipt is malformed")
        else:
            if condition_receipt.get("validation_result") != "accepted":
                reasons.append("condition-card evidence requires pilot review")
            if condition_receipt.get("dispatch_authority") is not False:
                reasons.append("condition-card extractor claimed dispatch authority")
            if condition_receipt.get("trace_id") != state.get("mission_trace_id"):
                reasons.append("condition-card receipt trace does not match the mission")
        if not isinstance(condition_evidence, Mapping):
            reasons.append("validator-approved condition evidence is missing")
        elif isinstance(condition_receipt, Mapping):
            if condition_evidence.get("artifact_sha256") != condition_receipt.get("artifact_sha256"):
                reasons.append("condition evidence digest does not match its model receipt")
            if condition_evidence.get("dispatch_authority") is not False:
                reasons.append("condition evidence claimed dispatch authority")
        if not isinstance(flight_plan, Mapping):
            reasons.append("condition-driven plan revision is missing")
        else:
            rejected_plan = flight_plan.get("rejected_plan")
            corrected_plan = flight_plan.get("corrected_plan")
            if not isinstance(rejected_plan, Mapping) or (
                rejected_plan.get("landing_sector"), rejected_plan.get("status")
            ) != ("east", "rejected"):
                reasons.append("plan v1 east-sector rejection is invalid")
            if not isinstance(corrected_plan, Mapping) or (
                corrected_plan.get("landing_sector"), corrected_plan.get("status")
            ) != ("west", "proposed_pending_pilot"):
                reasons.append("plan v2 west-sector proposal is invalid")
            if flight_plan.get("dispatch_authority") is not False:
                reasons.append("condition-driven plan claimed dispatch authority")
        if "EAST" not in upper_briefing or "WEST" not in upper_briefing:
            reasons.append("briefing omits the condition-driven east-to-west revision")
        if "REVIEW" not in upper_briefing and "PENDING" not in upper_briefing:
            reasons.append("west-sector proposal is not held for pilot review")

    return VerificationDecision(not reasons, tuple(reasons))


def guard_dispatch(callback_context: CallbackContext) -> types.Content | None:
    """ADK before-agent callback that skips FollowingAgent on any rejection."""
    decision = assess_dispatch_readiness(callback_context.state)
    callback_context.state["dispatch_authorized"] = decision.approved
    callback_context.state["verification_gate"] = decision.as_dict()
    emit_panel("verification_gate", decision.as_dict())

    if decision.approved:
        emit_step("VerifierGate", "approved", "Deterministic provenance checks passed; handoff unlocked.")
        return None

    detail = "; ".join(decision.reasons)
    emit_step("VerifierGate", "halted", f"Follower-room handoff blocked: {detail}.")
    return types.Content(
        role="model",
        parts=[types.Part(text=f"HANDOFF HALTED — {detail}.")],
    )
