"""Deterministic safety gate between the semantic Verifier and DispatchAgent.

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
    """Fail closed unless semantic approval and provenance invariants agree."""
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
    if not briefing.rstrip().endswith("NOT FOR OPERATIONAL USE."):
        reasons.append("operational-use disclaimer is missing")

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

    return VerificationDecision(not reasons, tuple(reasons))


def guard_dispatch(callback_context: CallbackContext) -> types.Content | None:
    """ADK before-agent callback that skips DispatchAgent on any rejection."""
    decision = assess_dispatch_readiness(callback_context.state)
    callback_context.state["dispatch_authorized"] = decision.approved
    callback_context.state["verification_gate"] = decision.as_dict()
    emit_panel("verification_gate", decision.as_dict())

    if decision.approved:
        emit_step("VerifierGate", "approved", "Deterministic provenance checks passed; dispatch unlocked.")
        return None

    detail = "; ".join(decision.reasons)
    emit_step("VerifierGate", "halted", f"Dispatch blocked: {detail}.")
    return types.Content(
        role="model",
        parts=[types.Part(text=f"DISPATCH HALTED — {detail}.")],
    )
