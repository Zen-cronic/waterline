"use client";
import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { MapView, type MapHandle } from "@/components/MapView";

type Step = { agent: string; kind: string; detail: string };
type DispatchResult = {
  to?: string;
  channel?: string;
  sent?: boolean;
  duplicate_suppressed?: boolean;
};
type SourceReceipt = {
  product: "notam" | "metar";
  source_mode: "live" | "local_frozen_capture";
  source_url: string;
  source_ref: string;
  records: number;
  parsed: number;
};
type Provenance = { site: string; notam: SourceReceipt; metar: SourceReceipt };
type Mission = {
  mission_id: string;
  owner_ref: string;
  trace_id: string;
  status: "proposed" | "rejected" | "awaiting_attestation" | "corrected" | "accepted" | "dispatched";
};
type StateEvent = {
  event_id: string;
  from_status?: string | null;
  to_status: Mission["status"];
  event_type: string;
  reason_code: string;
  trace_id: string;
  evidence?: Record<string, unknown>;
};
type ModelReceipt = {
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
type ConditionCardPanel = {
  image_url: string;
  validation_result: "accepted" | "review_required";
  reason_codes: string[];
  model_receipt: ModelReceipt;
};
type QuarantineReceipt = {
  receipt_id: string;
  category: string;
  content_sha256: string;
  action: string;
  dispatch_authority: false;
  trace_id: string;
};
type PlanRevision = {
  rejected_plan: { plan_id: string; landing_sector: string; reason_code: string };
  corrected_plan: { plan_id: string; landing_sector: string; status: string };
  dispatch_authority: false;
  trace_id: string;
};
const LAKES = [
  "Lady Evelyn Lake", "Lake Temagami", "Biscotasi Lake", "Wabikon Lake", "Smoothwater Lake",
];

export default function Page() {
  const mapRef = useRef<MapHandle>(null);
  const [dep, setDep] = useState("CYYZ");
  const [dst, setDst] = useState("Lady Evelyn Lake");
  const [alt, setAlt] = useState(3500);
  const [email, setEmail] = useState("");
  const [eta, setEta] = useState("16:00Z");
  const [grace, setGrace] = useState(60);
  const [running, setRunning] = useState(false);
  const [attesting, setAttesting] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [recoverable, setRecoverable] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);
  const [brief, setBrief] = useState("");
  const [verdict, setVerdict] = useState<{ ok: boolean; text: string } | null>(null);
  const [prov, setProv] = useState<Provenance | null>(null);
  const [dispatch, setDispatch] = useState<DispatchResult | null>(null);
  const [mission, setMission] = useState<Mission | null>(null);
  const [timeline, setTimeline] = useState<StateEvent[]>([]);
  const [conditionCard, setConditionCard] = useState<ConditionCardPanel | null>(null);
  const [quarantine, setQuarantine] = useState<QuarantineReceipt | null>(null);
  const [planRevision, setPlanRevision] = useState<PlanRevision | null>(null);
  const [error, setError] = useState("");

  function appendEvent(event?: StateEvent) {
    if (!event?.event_id) return;
    setTimeline((current) => current.some((item) => item.event_id === event.event_id)
      ? current : [...current, event]);
  }

  async function consumeMissionStream(res: Response) {
    if (!res.ok || !res.body) throw new Error(`Mission command failed (${res.status})`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const frames = buf.split("\n\n");
      buf = frames.pop() ?? "";
      for (const f of frames) {
        const line = f.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        let ev: any; try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
        if (ev.type === "layer") mapRef.current?.setLayer(ev);
        else if (ev.type === "step") setSteps((s) => [...s, ev]);
        else if (ev.type === "mission") {
          const next = ev as Mission & { event?: StateEvent };
          setMission(next);
          appendEvent(next.event);
          sessionStorage.setItem("waterline:last-mission", next.mission_id);
          if (next.status !== "rejected") setRecoverable(false);
        } else if (ev.type === "recovery") {
          setRecoverable(ev.available === true);
          if (ev.reasons?.length) setError(ev.reasons.join("; "));
        } else if (ev.type === "error") setError(ev.detail ?? "Briefing failed");
        else if (ev.type === "authority") setError((ev.reasons ?? []).join("; "));
        else if (ev.type === "panel" && ev.key === "condition_card")
          setConditionCard(ev.value as ConditionCardPanel);
        else if (ev.type === "panel" && ev.key === "quarantine")
          setQuarantine(ev.value as QuarantineReceipt);
        else if (ev.type === "panel" && ev.key === "plan_revision")
          setPlanRevision(ev.value as PlanRevision);
        else if (ev.type === "panel" && ev.key === "provenance") setProv(ev.value as Provenance);
        else if (ev.type === "panel" && ev.key === "dispatch") setDispatch(ev.value);
        else if (ev.type === "agent" && ev.final && ev.author === "BriefingComposer") setBrief(ev.text);
        else if (ev.type === "agent" && ev.final && ev.author === "Verifier")
          setVerdict({ ok: ev.text.trim().toUpperCase().startsWith("APPROVED"), text: ev.text });
      }
    }
  }

  useEffect(() => {
    const missionId = sessionStorage.getItem("waterline:last-mission");
    if (!missionId) return;
    let active = true;
    void fetch(`/api/waterline/missions/${missionId}`, { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) return;
        const restored = await res.json();
        if (!active) return;
        setMission({
          mission_id: restored.mission.mission_id,
          owner_ref: restored.mission.owner_ref,
          trace_id: restored.mission.trace_id,
          status: restored.mission.status,
        });
        setTimeline(restored.events);
        const evaluated = restored.events.find(
          (event: StateEvent) => event.event_type === "condition_card_evaluated",
        ) as StateEvent | undefined;
        const modelReceipt = evaluated?.evidence?.model_receipt as ModelReceipt | undefined;
        if (modelReceipt) {
          setConditionCard({
            image_url: "/evidence/lady-evelyn-condition-card-v1.png",
            validation_result: modelReceipt.validation_result,
            reason_codes: modelReceipt.reason_codes,
            model_receipt: modelReceipt,
          });
        }
        const quarantined = restored.events.find(
          (event: StateEvent) => event.event_type === "condition_card_quarantined",
        ) as StateEvent | undefined;
        const quarantineReceipt = quarantined?.evidence?.quarantine_receipt as
          QuarantineReceipt | undefined;
        if (quarantineReceipt) setQuarantine(quarantineReceipt);
        const revised = restored.events.find(
          (event: StateEvent) => event.event_type === "plan_revision_required",
        ) as StateEvent | undefined;
        const restoredPlan = revised?.evidence?.plan_revision as PlanRevision | undefined;
        if (restoredPlan) setPlanRevision(restoredPlan);
        const last = restored.events.at(-1) as StateEvent | undefined;
        setRecoverable(restored.mission.status === "rejected" &&
          last?.reason_code === "briefing_execution_failed");
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, []);

  async function run() {
    setRunning(true); setSteps([]); setBrief(""); setVerdict(null); setProv(null);
    setDispatch(null); setMission(null); setTimeline([]); setRecoverable(false); setError("");
    setConditionCard(null); setQuarantine(null); setPlanRevision(null);
    mapRef.current?.reset();
    try {
      const res = await fetch("/api/waterline/missions", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ departure: dep, destination: dst, cruise_alt_ft: alt }),
      });
      await consumeMissionStream(res);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Mission intake failed");
    } finally {
      setRunning(false);
    }
  }

  async function resume() {
    if (!mission || mission.status !== "rejected" || !recoverable) return;
    setRecovering(true); setRecoverable(false); setError("");
    try {
      const res = await fetch(`/api/waterline/missions/${mission.mission_id}/resume`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ confirm_resume: true }),
      });
      await consumeMissionStream(res);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Mission recovery failed");
    } finally {
      setRecovering(false);
    }
  }

  async function attest() {
    if (!mission || mission.status !== "awaiting_attestation") return;
    setAttesting(true); setError("");
    try {
      const res = await fetch(`/api/waterline/missions/${mission.mission_id}/attest`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          confirm_dispatch: true,
          responsible_email: email,
          eta,
          grace_min: grace,
        }),
      });
      const result = await res.json();
      if (!res.ok) throw new Error(result.detail?.message ?? result.detail ?? "Attestation failed");
      setMission({
        mission_id: result.mission_id, owner_ref: result.owner_ref,
        trace_id: result.trace_id, status: result.status,
      });
      for (const event of result.events ?? []) appendEvent(event);
      setDispatch({ sent: result.dispatch.sent, channel: result.dispatch.channel, to: email });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Attestation failed");
    } finally {
      setAttesting(false);
    }
  }

  return (
    <div className="app">
      <div className="left">
        <div className="brand">
          <h1><span>Waterline</span></h1>
          <p>A live, provenance-first briefing for curated water destinations with no station.</p>
        </div>
        <div className="form">
          <label>Departure (identifier)</label>
          <input value={dep} onChange={(e) => setDep(e.target.value.toUpperCase())} />
          <label>Destination (a lake with no station)</label>
          <select value={dst} onChange={(e) => setDst(e.target.value)}>
            {LAKES.map((l) => <option key={l}>{l}</option>)}
          </select>
          <div className="row">
            <div>
              <label>Cruise (ft)</label>
              <input type="number" value={alt} onChange={(e) => setAlt(+e.target.value)} />
            </div>
          </div>
          <button className="btn" onClick={run} disabled={running}>
            {running ? "Briefing…" : "Brief this flight"}
          </button>
        </div>
        <div className="feed">
          <div className="section-h">Agent roster</div>
          {steps.length === 0 && !running && <div className="prov">Seven agents brief in sequence.</div>}
          {mission && (
            <div className="mission-chip">
              <span>{mission.mission_id}</span>
              <strong>{mission.status.replaceAll("_", " ")}</strong>
              <small>authenticated owner {mission.owner_ref}</small>
              <small>trace {mission.trace_id}</small>
            </div>
          )}
          {timeline.length > 0 && (
            <div className="timeline" aria-label="Mission state timeline">
              <div className="section-h">Durable state timeline</div>
              {timeline.map((event) => (
                <div className={`state-event state-${event.to_status}`} key={event.event_id}>
                  <i aria-hidden="true" />
                  <div>
                    <strong>{event.to_status.replaceAll("_", " ")}</strong>
                    <span>{event.reason_code.replaceAll("_", " ")}</span>
                    <small>{event.event_id}</small>
                  </div>
                </div>
              ))}
            </div>
          )}
          {conditionCard && (
            <section className="evidence-card" aria-label="Prepared condition-card evidence">
              <div className="section-h">Prepared visual evidence</div>
              <Image
                src={conditionCard.image_url}
                width={1536}
                height={1024}
                sizes="(max-width: 760px) 100vw, 420px"
                alt="Synthetic Lady Evelyn Lake condition card showing the east cove obstructed and an untrusted OCR test note"
              />
              <div className="evidence-meta">
                <strong className={conditionCard.validation_result === "accepted" ? "safe" : "held"}>
                  {conditionCard.validation_result.replaceAll("_", " ")}
                </strong>
                <span>
                  {conditionCard.model_receipt.extractor === "fixture"
                    ? "Deterministic fixture extraction"
                    : "Gemini structured extraction"}
                  {` · ${Math.round(conditionCard.model_receipt.confidence * 100)}% confidence`}
                </span>
                <small>{conditionCard.model_receipt.receipt_id}</small>
                <small>sha256 {conditionCard.model_receipt.artifact_sha256}</small>
                <small>{conditionCard.model_receipt.schema_version}</small>
                <small>dispatch authority: false</small>
              </div>
            </section>
          )}
          {quarantine && (
            <div className="quarantine-card" role="status">
              <strong>Embedded instruction quarantined</strong>
              <span>No quarantined text entered trusted state or authority.</span>
              <small>{quarantine.receipt_id}</small>
              <small>content sha256 {quarantine.content_sha256}</small>
            </div>
          )}
          {planRevision && (
            <div className="plan-revision" aria-label="Deterministic plan revision">
              <div><small>PLAN V1</small><strong>EAST COVE</strong><span>rejected · obstruction</span></div>
              <b aria-hidden="true">→</b>
              <div><small>PLAN V2</small><strong>WEST COVE</strong><span>proposed · pilot review</span></div>
            </div>
          )}
          {mission?.status === "rejected" && recoverable && (
            <div className="recovery-card">
              <strong>Interrupted run retained</strong>
              <span>The failure is durable. Resume the same mission and session.</span>
              <button className="btn" onClick={resume} disabled={recovering}>
                {recovering ? "Recovering…" : "Resume retained mission"}
              </button>
            </div>
          )}
          {steps.map((s, i) => (
            <div className="step" key={i}>
              <span className={`who ${s.agent}`}>{s.agent}</span>
              <span>{s.detail}<span className="badge">{s.kind}</span></span>
            </div>
          ))}
          {brief && <><div className="section-h">Briefing</div><div className="brief">{brief}</div></>}
          {verdict && <div className={`verdict ${verdict.ok ? "ok" : "no"}`}>{verdict.text}</div>}
          {mission?.status === "awaiting_attestation" && (
            <div className="attestation">
              <div className="section-h">Human authority required</div>
              <p>The briefing is held. One authenticated pilot attestation may resume this mission and authorize one flight-following notice.</p>
              <label>Responsible person</label>
              <input type="email" placeholder="ops@example.com" value={email}
                onChange={(e) => setEmail(e.target.value)} />
              <div className="row">
                <div><label>ETA</label><input value={eta} onChange={(e) => setEta(e.target.value)} /></div>
                <div><label>Grace (min)</label><input type="number" value={grace}
                  onChange={(e) => setGrace(+e.target.value)} /></div>
              </div>
              <button className="btn" onClick={attest} disabled={attesting || !email || !eta}>
                {attesting ? "Attesting…" : "Attest & file one notice"}
              </button>
            </div>
          )}
          {dispatch && (
            <div className="verdict ok" style={{ background: "rgba(53,208,214,.12)", color: "#35d0d6", borderColor: "rgba(53,208,214,.3)" }}>
              {dispatch.duplicate_suppressed
                ? "✈ Itinerary already filed · retry sent no duplicate notice"
                : `✈ Itinerary filed · flight-following notice sent to ${dispatch.to} via ${dispatch.channel}`}
            </div>
          )}
          {prov && (
            <div className="prov">
              source: NOTAM {prov.notam.source_mode.replaceAll("_", " ")}
              {` · ${prov.notam.parsed}/${prov.notam.records} parsed`}
              {` · METAR ${prov.metar.source_mode.replaceAll("_", " ")}`}
              {` · ${prov.metar.parsed}/${prov.metar.records} stations`}
            </div>
          )}
          {error && <div className="verdict no">{error}</div>}
        </div>
      </div>
      <div className="right">
        <MapView ref={mapRef} />
        <div className="legend">
          <div className="k"><span className="dot" style={{ background: "#f2b45a" }} />route</div>
          <div className="k"><span className="dot" style={{ background: "#35d0d6" }} />corridor ±10 NM</div>
          <div className="k"><span className="dot" style={{ background: "#e6eef7" }} />NOTAM on route</div>
          <div className="k"><span className="dot" style={{ background: "#ef6b6b" }} />FIR-wide NOTAM</div>
          <div className="k"><span className="dot" style={{ background: "#57c98a" }} />source station</div>
        </div>
        <div className="footer">NOT FOR OPERATIONAL USE</div>
      </div>
    </div>
  );
}
