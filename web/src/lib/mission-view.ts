export type SourceReceipt = {
  product?: "notam" | "metar";
  source_mode: "live" | "local_frozen_capture";
  source_url?: string;
  source_ref: string;
  retrieved_at?: string;
  payload_sha256?: string;
  records: number;
  parsed: number;
};

export type Provenance = { site: string; notam: SourceReceipt; metar: SourceReceipt };

export type WeatherSource = {
  station_id: string;
  dist_nm: number;
  metar_raw?: string;
  wind_dir?: number | null;
  wind_kt?: number | null;
  gust_kt?: number | null;
  vis_sm?: number | null;
  ceiling_ft?: number | null;
};

export type Inference = {
  available: boolean;
  reach_nm?: number;
  confidence?: number;
  confidence_note?: string;
  inferred?: {
    wind_dir?: number | null;
    wind_kt?: number | null;
    gust_kt?: number | null;
    vis_sm?: number | null;
    ceiling_ft?: number | null;
    temp_c?: number | null;
    dew_c?: number | null;
  };
  sources?: WeatherSource[];
};

export type VerificationGate = { approved: boolean; reasons: string[] };

export type ModelReceipt = {
  receipt_id: string;
  source_ref: string;
  artifact_sha256: string;
  schema_version: string;
  extractor: string;
  validation_result: "accepted" | "review_required";
  confidence: number;
  reason_codes: string[];
  trace_id: string;
  dispatch_authority: false;
};

export type TrustedConditionEvidence = {
  card_id: string;
  lake_name: string;
  blocked_sector: string;
  obstruction: string;
  valid_from: string;
  valid_until: string;
  source_ref: string;
  artifact_sha256: string;
  confidence: number;
  dispatch_authority: false;
};

export type ConditionCardPanel = {
  image_url: string;
  validation_result: "accepted" | "review_required";
  reason_codes: string[];
  model_receipt: ModelReceipt;
  trusted_evidence?: TrustedConditionEvidence | null;
};

export type QuarantineReceipt = {
  receipt_id: string;
  category: string;
  content_sha256: string;
  action: string;
  dispatch_authority: false;
  trace_id: string;
};

export type PlanRevision = {
  rejected_plan: {
    plan_id: string;
    landing_sector: string;
    status?: string;
    reason_code: string;
  };
  corrected_plan: {
    plan_id: string;
    landing_sector: string;
    status: string;
    supersedes?: string;
  };
  dispatch_authority: false;
  trace_id: string;
};

export type DispatchReceipt = {
  receipt_id?: string;
  attestation_id?: string;
  mission_id?: string;
  trace_id?: string;
  recipient_redacted?: string;
  channel?: string;
  provider_reference?: string;
  provider_status?: string;
  sent?: boolean;
  duplicate_suppressed?: boolean;
  status?: "provider_accepted" | "delivered" | "completed" | "reconciliation_required";
  at_most_once?: boolean;
};

export type DegradedState = {
  title: string;
  reasons: string[];
  recoverable: boolean;
};

export type MissionStatus =
  | "proposed"
  | "rejected"
  | "awaiting_attestation"
  | "corrected"
  | "accepted"
  | "dispatched";

export type Mission = {
  mission_id: string;
  owner_ref: string;
  trace_id: string;
  status: MissionStatus;
};

export type StateEvent = {
  event_id: string;
  from_status?: string | null;
  to_status: MissionStatus;
  event_type: string;
  reason_code: string;
  trace_id: string;
  evidence?: Record<string, unknown>;
};

export type RestoredMissionView = {
  conditionCard: ConditionCardPanel | null;
  quarantine: QuarantineReceipt | null;
  planRevision: PlanRevision | null;
  inference: Inference | null;
  provenance: Provenance | null;
  briefingGate: VerificationGate | null;
  dispatchGate: VerificationGate | null;
  briefing: string;
  verdict: string;
  degraded: DegradedState | null;
  dispatch: DispatchReceipt | null;
};

function lastEvent(events: StateEvent[], eventType: string): StateEvent | undefined {
  return [...events].reverse().find((event) => event.event_type === eventType);
}

function objectValue<T>(value: unknown): T | null {
  return value && typeof value === "object" ? value as T : null;
}

export function deriveRestoredMissionView(
  mission: Mission,
  events: StateEvent[],
): RestoredMissionView {
  const evaluated = lastEvent(events, "condition_card_evaluated");
  const modelReceipt = objectValue<ModelReceipt>(evaluated?.evidence?.model_receipt);
  const trustedEvidence = objectValue<TrustedConditionEvidence>(
    evaluated?.evidence?.trusted_evidence,
  );
  const conditionCard = modelReceipt ? {
    image_url: "/evidence/lady-evelyn-condition-card-v1.png",
    validation_result: modelReceipt.validation_result,
    reason_codes: modelReceipt.reason_codes,
    model_receipt: modelReceipt,
    trusted_evidence: trustedEvidence,
  } satisfies ConditionCardPanel : null;

  const quarantineEvent = lastEvent(events, "condition_card_quarantined");
  const quarantine = objectValue<QuarantineReceipt>(
    quarantineEvent?.evidence?.quarantine_receipt,
  );
  const revisionEvent = lastEvent(events, "plan_revision_required");
  const planRevision = objectValue<PlanRevision>(
    revisionEvent?.evidence?.plan_revision ?? evaluated?.evidence?.plan_revision,
  );
  const proofEvent = lastEvent(events, "briefing_evidence_recorded");
  const proof = proofEvent?.evidence ?? {};
  const inference = objectValue<Inference>(proof.inference);
  const provenance = objectValue<Provenance>(proof.provenance);
  const briefingGate = objectValue<VerificationGate>(proof.briefing_gate);
  let dispatchGate = objectValue<VerificationGate>(proof.dispatch_gate);

  const failure = [...events].reverse().find((event) =>
    event.reason_code === "briefing_execution_failed" ||
    event.reason_code === "condition_card_review_required" ||
    event.reason_code === "visual_evidence_review_required"
  );
  const reasons = Array.isArray(failure?.evidence?.reasons)
    ? failure.evidence.reasons.filter((reason): reason is string => typeof reason === "string")
    : [];
  const degraded = failure ? {
    title: failure.reason_code === "briefing_execution_failed"
      ? "Agent run degraded"
      : "Visual evidence held for review",
    reasons: reasons.length ? reasons : [failure.reason_code.replaceAll("_", " ")],
    recoverable: failure.reason_code === "briefing_execution_failed",
  } : null;

  const dispatched = lastEvent(events, "dispatch_completed");
  const delivery = lastEvent(events, "delivery_status_updated");
  const replay = lastEvent(events, "dispatch_replayed");
  const dispatchFailure = lastEvent(events, "dispatch_failed");
  let dispatch: DispatchReceipt | null = null;
  if (dispatched) {
    const providerStatus = String(
      delivery?.evidence?.provider_status ?? dispatched.evidence?.provider_status ?? "",
    );
    const channel = String(dispatched.evidence?.channel ?? "");
    dispatch = {
      receipt_id: String(dispatched.evidence?.receipt_id ?? ""),
      attestation_id: String(dispatched.evidence?.attestation_id ?? ""),
      mission_id: mission.mission_id,
      trace_id: mission.trace_id,
      channel,
      provider_reference: String(dispatched.evidence?.provider_reference ?? ""),
      provider_status: providerStatus,
      recipient_redacted: String(dispatched.evidence?.recipient_redacted ?? ""),
      sent: true,
      duplicate_suppressed: replay?.evidence?.duplicate_suppressed === true,
      status: providerStatus === "delivered" || providerStatus === "read"
        ? "delivered" : channel === "sms" ? "provider_accepted" : "completed",
      at_most_once: true,
    };
    dispatchGate = { approved: true, reasons: [] };
  } else if (dispatchFailure?.evidence?.duplicate_suppressed === true) {
    dispatch = {
      receipt_id: String(dispatchFailure.evidence.receipt_id ?? ""),
      mission_id: mission.mission_id,
      trace_id: mission.trace_id,
      duplicate_suppressed: true,
      status: "reconciliation_required",
      at_most_once: true,
    };
  }

  return {
    conditionCard,
    quarantine,
    planRevision,
    inference,
    provenance,
    briefingGate,
    dispatchGate,
    briefing: typeof proof.briefing === "string" ? proof.briefing : "",
    verdict: typeof proof.semantic_verdict === "string" ? proof.semantic_verdict : "",
    degraded,
    dispatch,
  };
}
