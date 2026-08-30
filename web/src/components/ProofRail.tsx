import Image from "next/image";
import type { CSSProperties } from "react";

import type {
  ConditionCardPanel,
  DegradedState,
  DispatchReceipt,
  Inference,
  Mission,
  PlanRevision,
  Provenance,
  QuarantineReceipt,
  VerificationGate,
} from "@/lib/mission-view";

type ProofRailProps = {
  mission: Mission | null;
  conditionCard: ConditionCardPanel | null;
  quarantine: QuarantineReceipt | null;
  planRevision: PlanRevision | null;
  inference: Inference | null;
  provenance: Provenance | null;
  briefingGate: VerificationGate | null;
  dispatchGate: VerificationGate | null;
  degraded: DegradedState | null;
  dispatch: DispatchReceipt | null;
  followingActive?: boolean;
  onClose?: () => void;
  onReplayDispatch?: () => void;
  replayingDispatch?: boolean;
};

function short(value?: string, length = 18): string {
  if (!value) return "pending";
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

function metric(value: number | null | undefined, suffix: string): string {
  return typeof value === "number" ? `${value}${suffix}` : "—";
}

function GateCard({ label, gate }: { label: string; gate: VerificationGate | null }) {
  const state = gate ? (gate.approved ? "passed" : "held") : "waiting";
  return (
    <div className={`gate-card ${state}`}>
      <div><span>{label}</span><strong>{state}</strong></div>
      <p>{gate
        ? gate.approved ? "Deterministic checks passed." : gate.reasons.join(" · ")
        : "Awaiting evidence."}</p>
    </div>
  );
}

function AuthorityMap({
  mission, conditionCard, briefingGate, dispatchGate, dispatch,
}: Pick<ProofRailProps,
  "mission" | "conditionCard" | "briefingGate" | "dispatchGate" | "dispatch">) {
  const pilotAttested = mission && ["corrected", "accepted", "dispatched"].includes(mission.status);
  const deliveryState = dispatch && dispatch.status !== "reconciliation_required"
    ? "verified"
    : dispatch?.duplicate_suppressed ? "blocked" : "held";
  return (
    <section className="authority-map" aria-label="Authority Map">
      <div className="proof-section-title"><span>Authority Map</span><small>who can do what</small></div>
      <div className="authority-flow">
        <div className={`authority-node ${mission ? "active" : "waiting"}`}>
          <small>AUTHENTICATED INTAKE</small><strong>REQUEST</strong><span>owner-bound relay</span>
        </div>
        <b aria-hidden="true">→</b>
        <div className={`authority-node ${conditionCard || briefingGate ? "active" : "waiting"}`}>
          <small>GEMINI + ADK FLEET</small><strong>READ · PROPOSE</strong><span>authority = 0</span>
        </div>
        <b aria-hidden="true">→</b>
        <div className={`authority-node ${briefingGate?.approved ? "active" : "held"}`}>
          <small>DETERMINISTIC WRITER</small><strong>VALIDATE · RECORD</strong><span>policy-owned state</span>
        </div>
        <b aria-hidden="true">→</b>
        <div className={`authority-node ${pilotAttested ? "active" : "held"}`}>
          <small>AUTHENTICATED PILOT</small><strong>SOLE AUTHORIZE</strong><span>{pilotAttested ? "attested" : "required"}</span>
        </div>
        <b aria-hidden="true">→</b>
        <div className={`authority-node ${deliveryState}`}>
          <small>ROOM CLAIM</small><strong>AT-MOST-ONCE</strong><span>{deliveryState}</span>
        </div>
      </div>
      <div className={`authority-decision ${dispatchGate?.approved ? "passed" : "held"}`}>
        <span>handoff gate</span>
        <strong>{dispatchGate?.approved ? "AUTHORIZED BY POLICY + PILOT" : "HELD — AGENTS CANNOT OPEN A ROOM"}</strong>
      </div>
    </section>
  );
}

export function ProofRail(props: ProofRailProps) {
  const {
    mission, conditionCard, quarantine, planRevision, inference, provenance,
    briefingGate, dispatchGate, degraded, dispatch, followingActive,
    onClose, onReplayDispatch, replayingDispatch,
  } = props;
  const trusted = conditionCard?.trusted_evidence;
  const primary = inference?.sources?.[0];
  const confidence = Math.round((inference?.confidence ?? 0) * 100);
  const inferred = inference?.inferred;
  const planAccepted = mission?.status === "accepted" || mission?.status === "dispatched";

  return (
    <aside className="proof-rail" id="proof-panel" aria-label="Mission proof and authority">
      <header className="proof-header">
        <div><span>LIVE PROOF STACK</span><h2>Evidence → authority → consequence</h2></div>
        <div className="proof-header-actions">
          <strong className={`proof-status status-${mission?.status ?? "idle"}`}>
            {followingActive ? "FOLLOWING ACTIVE"
              : dispatch?.status === "room_ready" ? "HANDOFF READY"
              : mission?.status === "awaiting_attestation" ? "ATTESTATION REQUIRED"
              : mission?.status.replaceAll("_", " ") ?? "ready"}
          </strong>
          {onClose && (
            <button className="panel-close" type="button" aria-label="Hide proof panel"
              aria-controls="proof-panel" aria-expanded="true" onClick={onClose}>
              <span aria-hidden="true">›</span>
            </button>
          )}
        </div>
      </header>

      <AuthorityMap
        mission={mission}
        conditionCard={conditionCard}
        briefingGate={briefingGate}
        dispatchGate={dispatchGate}
        dispatch={dispatch}
      />

      {degraded && (
        <section className="degraded-card" role="alert">
          <div className="proof-section-title"><span>{degraded.title}</span><small>{degraded.recoverable ? "same-session recovery" : "fail closed"}</small></div>
          {degraded.reasons.map((reason) => <p key={reason}>{reason}</p>)}
          <strong>No mutation · no handoff</strong>
        </section>
      )}

      {conditionCard && (
        <section className="proof-card visual-proof" aria-label="Prepared condition-card extraction">
          <div className="proof-section-title"><span>01 · Visual extraction</span><small>{conditionCard.model_receipt.extractor === "fixture" ? "fixture replay" : "Vertex Gemini"}</small></div>
          <Image
            src={conditionCard.image_url}
            width={1536}
            height={1024}
            unoptimized
            loading="eager"
            sizes="(max-width: 1100px) 100vw, 420px"
            alt="Synthetic Lady Evelyn Lake condition card showing the east cove obstructed and an untrusted OCR test note"
          />
          <div className="receipt-head">
            <strong className={conditionCard.validation_result === "accepted" ? "safe" : "held"}>
              {conditionCard.validation_result.replaceAll("_", " ")}
            </strong>
            <span>{Math.round(conditionCard.model_receipt.confidence * 100)}% model confidence</span>
          </div>
          {trusted ? (
            <dl className="evidence-grid">
              <div><dt>lake</dt><dd>{trusted.lake_name}</dd></div>
              <div><dt>card</dt><dd>{trusted.card_id}</dd></div>
              <div><dt>trusted fact</dt><dd className="danger">{trusted.blocked_sector.toUpperCase()} COVE OBSTRUCTED</dd></div>
              <div><dt>obstruction</dt><dd>{trusted.obstruction}</dd></div>
              <div className="wide"><dt>validity</dt><dd>{trusted.valid_from} → {trusted.valid_until}</dd></div>
            </dl>
          ) : (
            <p className="held-copy">{conditionCard.reason_codes.join(" · ") || "No visual fact entered trusted state."}</p>
          )}
          <div className="receipt-lines">
            <code>{conditionCard.model_receipt.receipt_id}</code>
            <code>sha256 {conditionCard.model_receipt.artifact_sha256}</code>
            <code>{conditionCard.model_receipt.schema_version}</code>
            <code>dispatch_authority=false · trace {short(conditionCard.model_receipt.trace_id, 24)}</code>
          </div>
        </section>
      )}

      {(quarantine || planRevision) && (
        <section className="exception-stack" aria-label="Validated exceptions">
          {quarantine && (
            <div className="quarantine-card" role="status">
              <div className="proof-section-title"><span>02 · Hostile text quarantined</span><small>hash only</small></div>
              <p>No quarantined text entered trusted state, memory, transitions, or authority.</p>
              <code>{quarantine.receipt_id}</code>
              <code>content sha256 {quarantine.content_sha256}</code>
            </div>
          )}
          {planRevision && (
            <div className={`plan-revision ${planAccepted ? "accepted" : ""}`} aria-label="Deterministic plan revision">
              <div><small>PLAN V1</small><strong>EAST COVE</strong><span>rejected · obstruction</span></div>
              <b aria-hidden="true">→</b>
              <div><small>PLAN V2</small><strong>WEST COVE</strong><span>{planAccepted ? "accepted · pilot attested" : "proposed · pilot review"}</span></div>
            </div>
          )}
        </section>
      )}

      {inference && (
        <section className="proof-card inference-card" aria-label="Station-less weather inference">
          <div className="proof-section-title"><span>03 · Inference, not measurement</span><small>{primary?.station_id ?? "no source"}</small></div>
          <div className="confidence-row">
            <div className="confidence-ring" style={{ "--confidence": `${confidence}%` } as CSSProperties}>
              <strong>{confidence}%</strong><span>confidence</span>
            </div>
            <div><strong>Nearest station {metric(inference.reach_nm, " NM")} away</strong><p>{inference.confidence_note}</p></div>
          </div>
          <div className="weather-grid">
            <div><small>WIND</small><strong>{metric(inferred?.wind_dir, "°")} / {metric(inferred?.wind_kt, " kt")}</strong></div>
            <div><small>VISIBILITY</small><strong>{metric(inferred?.vis_sm, " SM")}</strong></div>
            <div><small>CEILING</small><strong>{metric(inferred?.ceiling_ft, " ft")}</strong></div>
          </div>
          {primary?.metar_raw && <code className="raw-source">{primary.metar_raw}</code>}
        </section>
      )}

      {provenance && (
        <section className="proof-card provenance-card" aria-label="Source provenance receipts">
          <div className="proof-section-title"><span>04 · Source provenance</span><small>{provenance.site}</small></div>
          {(["notam", "metar"] as const).map((product) => {
            const receipt = provenance[product];
            return (
              <div className="source-receipt" key={product}>
                <div><strong>{product.toUpperCase()}</strong><span className={receipt.source_mode === "live" ? "live" : "fixture"}>{receipt.source_mode.replaceAll("_", " ")}</span></div>
                <p>{receipt.parsed}/{receipt.records} parsed · {receipt.source_ref}</p>
                <code>{receipt.payload_sha256 ? `sha256 ${short(receipt.payload_sha256, 32)}` : short(receipt.source_url, 42)}</code>
              </div>
            );
          })}
        </section>
      )}

      {(briefingGate || dispatchGate) && (
        <section className="proof-card gate-stack" aria-label="Deterministic gate decisions">
          <div className="proof-section-title"><span>05 · Deterministic gates</span><small>model prose is insufficient</small></div>
          <GateCard label="Briefing integrity" gate={briefingGate} />
          <GateCard label="Handoff authority" gate={dispatchGate} />
        </section>
      )}

      {dispatch && (
        <section className={`dispatch-receipt ${dispatch.status !== "reconciliation_required" ? "sent" : "held"}`} aria-label="Verified handoff receipt">
          <div className="proof-section-title"><span>{dispatch.status !== "reconciliation_required" ? "06 · Verified consequence" : "06 · Replay suppressed"}</span><small>at-most-once</small></div>
          <strong>{followingActive ? "FOLLOWING ACTIVE · LIVE VIA FIRESTORE"
            : dispatch.status === "room_ready" ? "HANDOFF READY · WAITING FOR FOLLOWER"
            : dispatch.status === "completed" ? "LEGACY HANDOFF COMPLETED"
            : "ORIGINAL RECEIPT RETURNED"}</strong>
          <p>{dispatch.status !== "reconciliation_required"
            ? dispatch.status === "room_ready"
              ? "One ephemeral coordination room is bound to the durable Cloud SQL receipt."
              : "Historical handoff restored from the original receipt."
            : "The durable claim could not be reconstructed; mission authority did not change."}</p>
          {dispatch.provider_reference && <code>room {dispatch.provider_reference}</code>}
          <code>receipt {dispatch.receipt_id || "pending reconciliation"}</code>
          {dispatch.attestation_id && <code>attestation {dispatch.attestation_id}</code>}
          <code>trace {dispatch.trace_id}</code>
          {dispatch.duplicate_suppressed && <strong>REPLAY SUPPRESSED · ORIGINAL RECEIPT RETURNED</strong>}
          {onReplayDispatch && !dispatch.duplicate_suppressed && dispatch.status !== "reconciliation_required" && (
            <button className="receipt-replay" onClick={onReplayDispatch} disabled={replayingDispatch}>
              {replayingDispatch ? "Verifying replay safety…" : "Replay handoff · prove one room"}
            </button>
          )}
        </section>
      )}
    </aside>
  );
}
