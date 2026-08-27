import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  deriveRestoredMissionView,
  type Mission,
  type StateEvent,
} from "../src/lib/mission-view";

const mission: Mission = {
  mission_id: "mission-0123456789abcdefabcd",
  owner_ref: "pilot-owner",
  trace_id: "trace-proof-1",
  status: "awaiting_attestation",
};

const modelReceipt = {
  receipt_id: "model-receipt-proof",
  source_ref: "prepared://lady-evelyn/condition-card-v1",
  artifact_sha256: "a".repeat(64),
  schema_version: "waterline.condition-card.extraction.v1",
  extractor: "fixture",
  validation_result: "accepted" as const,
  confidence: 0.98,
  reason_codes: [],
  trace_id: mission.trace_id,
  dispatch_authority: false as const,
};

function event(
  event_id: string,
  event_type: string,
  reason_code: string,
  evidence: Record<string, unknown>,
  to_status: Mission["status"] = "proposed",
): StateEvent {
  return {
    event_id,
    event_type,
    reason_code,
    evidence,
    trace_id: mission.trace_id,
    to_status,
  };
}

test("restored proof keeps extraction, inference, provenance, gates, and quarantine visible", () => {
  const plan = {
    rejected_plan: { plan_id: "plan-v1", landing_sector: "east", status: "rejected", reason_code: "east_cove_obstructed" },
    corrected_plan: { plan_id: "plan-v2", landing_sector: "west", status: "proposed_pending_pilot" },
    dispatch_authority: false as const,
    trace_id: mission.trace_id,
  };
  const events: StateEvent[] = [
    event("event-1", "condition_card_evaluated", "condition_card_validated", {
      model_receipt: modelReceipt,
      trusted_evidence: {
        card_id: "WL-LEL-20260826-A", lake_name: "Lady Evelyn Lake",
        blocked_sector: "east", obstruction: "LOG BOOM ACROSS APPROACH",
        valid_from: "2026-08-26T12:00:00+00:00",
        valid_until: "2026-09-07T23:59:00+00:00",
        source_ref: modelReceipt.source_ref, artifact_sha256: modelReceipt.artifact_sha256,
        confidence: 0.98, dispatch_authority: false,
      },
      plan_revision: plan,
    }),
    event("event-2", "condition_card_quarantined", "embedded_instruction_excluded", {
      quarantine_receipt: {
        receipt_id: "quarantine-proof", category: "embedded_instruction",
        content_sha256: "b".repeat(64), action: "excluded_from_trusted_state",
        dispatch_authority: false, trace_id: mission.trace_id,
      },
    }),
    event("event-3", "briefing_evidence_recorded", "deterministic_briefing_gate_passed", {
      briefing: "INFERRED from CYXR. NOT FOR OPERATIONAL USE.",
      semantic_verdict: "APPROVED — traceable.",
      inference: {
        available: true, confidence: 0.14, reach_nm: 27.8,
        sources: [{ station_id: "CYXR", dist_nm: 27.8, metar_raw: "CYXR 261200Z" }],
      },
      provenance: {
        site: "CZYZ",
        notam: { source_mode: "live", source_ref: "navcanada:notam", records: 471, parsed: 471 },
        metar: { source_mode: "live", source_ref: "navcanada:metar", records: 38, parsed: 38 },
      },
      briefing_gate: { approved: true, reasons: [] },
      dispatch_gate: { approved: false, reasons: ["authenticated pilot attestation is missing"] },
    }),
    event("event-4", "plan_revision_required", "east_cove_obstructed", {
      plan_revision: plan,
    }, "rejected"),
  ];

  const view = deriveRestoredMissionView(mission, events);
  assert.equal(view.conditionCard?.trusted_evidence?.blocked_sector, "east");
  assert.equal(view.conditionCard?.model_receipt.dispatch_authority, false);
  assert.equal(view.quarantine?.action, "excluded_from_trusted_state");
  assert.equal(view.planRevision?.corrected_plan.landing_sector, "west");
  assert.equal(view.inference?.sources?.[0].station_id, "CYXR");
  assert.equal(view.provenance?.notam.records, 471);
  assert.equal(view.briefingGate?.approved, true);
  assert.equal(view.dispatchGate?.approved, false);
  assert.match(view.briefing, /NOT FOR OPERATIONAL USE/);
  assert.match(view.verdict, /^APPROVED/);
});

test("restored dispatch and degraded evidence remain explicit", () => {
  const degraded = deriveRestoredMissionView(
    { ...mission, status: "rejected" },
    [event("event-fail", "proposal_rejected", "briefing_execution_failed", {
      reasons: ["worker timeout"], recoverable: true,
    }, "rejected")],
  );
  assert.equal(degraded.degraded?.recoverable, true);
  assert.deepEqual(degraded.degraded?.reasons, ["worker timeout"]);

  const dispatched = deriveRestoredMissionView(
    { ...mission, status: "dispatched" },
    [event("event-send", "dispatch_completed", "verified_notice_receipt", {
      receipt_id: "receipt-proof", attestation_id: "attestation-proof", channel: "test",
    }, "dispatched")],
  );
  assert.equal(dispatched.dispatch?.status, "sent");
  assert.equal(dispatched.dispatch?.receipt_id, "receipt-proof");
  assert.equal(dispatched.dispatchGate?.approved, true);

  const replay = deriveRestoredMissionView(
    { ...mission, status: "accepted" },
    [event("event-replay", "dispatch_failed", "notice_not_completed", {
      duplicate_suppressed: true, receipt_id: "receipt-proof",
    }, "accepted")],
  );
  assert.equal(replay.dispatch?.status, "reconciliation_required");
  assert.equal(replay.dispatch?.duplicate_suppressed, true);
});

test("judge-facing surface names every required proof state", () => {
  const proofRail = readFileSync(new URL("../src/components/ProofRail.tsx", import.meta.url), "utf8");
  const page = readFileSync(new URL("../src/app/page.tsx", import.meta.url), "utf8");
  for (const label of [
    "Visual extraction", "Hostile text quarantined", "Inference, not measurement",
    "Source provenance", "Deterministic gates", "Authority Map",
    "SOLE AUTHORIZE", "AT-MOST-ONCE", "Verified consequence", "Replay suppressed",
  ]) assert.match(proofRail, new RegExp(label));
  assert.match(page, /Human authority required/);
  assert.match(page, /Review required — dispatch held/);
  assert.match(page, /conditionCard\?\.validation_result === "accepted"/);
  assert.match(page, /briefingGate\?\.approved === true/);
  assert.match(page, /Interrupted run retained/);
  assert.match(page, /DETERMINISTIC CONSEQUENCE/);
});
