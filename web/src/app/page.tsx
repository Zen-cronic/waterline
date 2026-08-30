"use client";
import { useEffect, useRef, useState } from "react";
import { MapView, type MapHandle } from "@/components/MapView";
import { FollowerRoom } from "@/components/FollowerRoom";
import { MarkdownBrief } from "@/components/MarkdownBrief";
import { ProofRail } from "@/components/ProofRail";
import {
  deriveRestoredMissionView,
  type ConditionCardPanel,
  type DegradedState,
  type DispatchReceipt,
  type HandoffInvitation,
  type Inference,
  type Mission,
  type PlanRevision,
  type Provenance,
  type QuarantineReceipt,
  type StateEvent,
  type VerificationGate,
} from "@/lib/mission-view";

type Step = { agent: string; kind: string; detail: string };
const LAKES = [
  "Lady Evelyn Lake", "Lake Temagami", "Biscotasi Lake", "Wabikon Lake", "Smoothwater Lake",
];

export default function Page() {
  const mapRef = useRef<MapHandle>(null);
  const [dep, setDep] = useState("CYYZ");
  const [dst, setDst] = useState("Lady Evelyn Lake");
  const [alt, setAlt] = useState(3500);
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
  const [inference, setInference] = useState<Inference | null>(null);
  const [briefingGate, setBriefingGate] = useState<VerificationGate | null>(null);
  const [dispatchGate, setDispatchGate] = useState<VerificationGate | null>(null);
  const [degraded, setDegraded] = useState<DegradedState | null>(null);
  const [dispatch, setDispatch] = useState<DispatchReceipt | null>(null);
  const [handoff, setHandoff] = useState<HandoffInvitation | null>(null);
  const [followingActive, setFollowingActive] = useState(false);
  const [mission, setMission] = useState<Mission | null>(null);
  const [timeline, setTimeline] = useState<StateEvent[]>([]);
  const [conditionCard, setConditionCard] = useState<ConditionCardPanel | null>(null);
  const [quarantine, setQuarantine] = useState<QuarantineReceipt | null>(null);
  const [planRevision, setPlanRevision] = useState<PlanRevision | null>(null);
  const [error, setError] = useState("");
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [missionPanelOpen, setMissionPanelOpen] = useState(true);
  const [proofPanelOpen, setProofPanelOpen] = useState(true);
  const [followerPanelOpen, setFollowerPanelOpen] = useState(true);
  const [origin, setOrigin] = useState("");

  useEffect(() => {
    const saved = localStorage.getItem("waterline:theme");
    const next = saved === "light" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    setOrigin(window.location.origin);
  }, []);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    localStorage.setItem("waterline:theme", next);
  }

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
          const reasons = ev.reasons?.length ? ev.reasons : ["Briefing execution failed"];
          setDegraded({ title: "Agent run degraded", reasons, recoverable: ev.available === true });
          setError(reasons.join("; "));
        } else if (ev.type === "error") {
          const detail = ev.detail ?? "Briefing failed";
          setError(detail);
          setDegraded({ title: "Agent run degraded", reasons: [detail], recoverable: false });
        }
        else if (ev.type === "authority") {
          const gate = { approved: ev.approved === true, reasons: ev.reasons ?? [] };
          setDispatchGate(gate);
          if (!gate.approved) setDegraded({
            title: "Visual evidence held for review", reasons: gate.reasons,
            recoverable: false,
          });
        }
        else if (ev.type === "panel" && ev.key === "condition_card")
          setConditionCard(ev.value as ConditionCardPanel);
        else if (ev.type === "panel" && ev.key === "quarantine")
          setQuarantine(ev.value as QuarantineReceipt);
        else if (ev.type === "panel" && ev.key === "plan_revision")
          setPlanRevision(ev.value as PlanRevision);
        else if (ev.type === "panel" && ev.key === "inference")
          setInference(ev.value as Inference);
        else if (ev.type === "panel" && ev.key === "provenance") setProv(ev.value as Provenance);
        else if (ev.type === "panel" && ev.key === "verification_gate")
          setDispatchGate(ev.value as VerificationGate);
        else if (ev.type === "panel" && ev.key === "mission_proof") {
          setBriefingGate(ev.value.briefing_gate as VerificationGate);
          setDispatchGate(ev.value.dispatch_gate as VerificationGate);
          if (ev.value.inference) setInference(ev.value.inference as Inference);
          if (ev.value.provenance) setProv(ev.value.provenance as Provenance);
        }
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
        const view = deriveRestoredMissionView(restored.mission, restored.events);
        setConditionCard(view.conditionCard);
        setQuarantine(view.quarantine);
        setPlanRevision(view.planRevision);
        setInference(view.inference);
        setProv(view.provenance);
        setBriefingGate(view.briefingGate);
        setDispatchGate(view.dispatchGate);
        setBrief(view.briefing);
        if (view.verdict) setVerdict({
          ok: view.verdict.trim().toUpperCase().startsWith("APPROVED"),
          text: view.verdict,
        });
        setDegraded(view.degraded);
        setDispatch(view.dispatch);
        setHandoff(restored.handoff ?? null);
        const attestation = (restored.events as StateEvent[]).findLast(
          (event) => event.event_type === "pilot_attestation_recorded",
        );
        if (typeof attestation?.evidence?.eta === "string") setEta(attestation.evidence.eta);
        if (typeof attestation?.evidence?.grace_min === "number") setGrace(attestation.evidence.grace_min);
        const last = restored.events.at(-1) as StateEvent | undefined;
        setRecoverable(restored.mission.status === "rejected" &&
          last?.reason_code === "briefing_execution_failed");
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, []);

  async function run() {
    setRunning(true); setSteps([]); setBrief(""); setVerdict(null); setProv(null);
    setDispatch(null); setHandoff(null); setFollowingActive(false); setMission(null); setTimeline([]); setRecoverable(false); setError("");
    setConditionCard(null); setQuarantine(null); setPlanRevision(null);
    setInference(null); setBriefingGate(null); setDispatchGate(null); setDegraded(null);
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
    setRecovering(true); setRecoverable(false); setError(""); setDegraded(null);
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
          confirm_handoff: true,
          eta,
          grace_min: grace,
        }),
      });
      const result = await res.json();
      if (!res.ok) {
        const detail = result.detail;
        if (detail?.duplicate_suppressed) {
          setDispatch({
            receipt_id: detail.receipt_id,
            mission_id: detail.mission_id,
            trace_id: detail.trace_id,
            duplicate_suppressed: true,
            status: "reconciliation_required",
            at_most_once: detail.at_most_once === true,
          });
          setDispatchGate({ approved: false, reasons: [detail.message] });
        }
        throw new Error(detail?.message ?? detail ?? "Attestation failed");
      }
      setMission({
        mission_id: result.mission_id, owner_ref: result.owner_ref,
        trace_id: result.trace_id, status: result.status,
      });
      for (const event of result.events ?? []) appendEvent(event);
      setDispatchGate(result.authority as VerificationGate);
      setDispatch(result.dispatch as DispatchReceipt);
      setHandoff(result.handoff as HandoffInvitation);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Attestation failed");
    } finally {
      setAttesting(false);
    }
  }

  async function replayHandoff() {
    if (!mission || mission.status !== "dispatched" || !dispatch?.receipt_id) return;
    setAttesting(true); setError("");
    try {
      const res = await fetch(`/api/waterline/missions/${mission.mission_id}/attest`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ confirm_handoff: true, eta, grace_min: grace }),
      });
      const result = await res.json();
      if (!res.ok) throw new Error(result.detail?.message ?? result.detail ?? "Replay safety check failed");
      setMission({
        mission_id: result.mission_id, owner_ref: result.owner_ref,
        trace_id: result.trace_id, status: result.status,
      });
      for (const event of result.events ?? []) appendEvent(event);
      setDispatchGate(result.authority as VerificationGate);
      setDispatch(result.dispatch as DispatchReceipt);
      setHandoff(result.handoff as HandoffInvitation);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Replay safety check failed");
    } finally {
      setAttesting(false);
    }
  }

  const stateTimeline = timeline.filter((event) =>
    event.from_status !== event.to_status ||
    event.event_type === "recovery_started" ||
    event.event_type === "flight_memory_written" ||
    event.event_type === "flight_memory_failed"
  );
  const canAttest = mission?.status === "awaiting_attestation" &&
    conditionCard?.validation_result === "accepted" &&
    planRevision !== null && briefingGate?.approved === true;
  const etaValid = /^(?:[01][0-9]|2[0-3]):[0-5][0-9]Z$/.test(eta);
  const terminalLabel = followingActive
    ? "FOLLOWING ACTIVE"
    : handoff ? "HANDOFF READY"
    : mission?.status === "awaiting_attestation" ? "ATTESTATION REQUIRED"
    : mission?.status.replaceAll("_", " ") ?? "READY";

  return (
    <div className={`app ${missionPanelOpen ? "" : "mission-panel-collapsed"} ${proofPanelOpen ? "" : "proof-panel-collapsed"}`}>
      <div className="left" id="mission-panel">
        <div className="brand">
          <div className="brand-row">
            <h1><span>Waterline</span></h1>
            <div className="brand-actions">
              <button className="theme-toggle" type="button" onClick={toggleTheme}
                aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
                aria-pressed={theme === "light"}>
                {theme === "dark" ? "LIGHT" : "DARK"}
              </button>
              <button className="panel-close" type="button"
                aria-label="Hide mission panel" aria-controls="mission-panel" aria-expanded={missionPanelOpen}
                onClick={() => setMissionPanelOpen(false)}>
                <span aria-hidden="true">‹</span>
              </button>
            </div>
          </div>
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
          {steps.length === 0 && !running && <div className="prov">Eight agents brief in sequence.</div>}
          {mission && (
            <div className="mission-chip">
              <span>{mission.mission_id}</span>
              <strong>{terminalLabel}</strong>
              <small>authenticated owner {mission.owner_ref}</small>
              <small>trace {mission.trace_id}</small>
            </div>
          )}
          {stateTimeline.length > 0 && (
            <div className="timeline" aria-label="Mission state timeline">
              <div className="section-h">Durable state timeline</div>
              {stateTimeline.map((event) => (
                <div className={`state-event state-${event.to_status}`} key={event.event_id}>
                  <i aria-hidden="true" />
                  <div>
                    <strong>{event.event_type === "flight_memory_written"
                      ? "MEMORY WRITTEN"
                      : event.event_type === "flight_memory_failed"
                        ? "MEMORY DEGRADED"
                        : event.to_status.replaceAll("_", " ")}</strong>
                    <span>{event.reason_code.replaceAll("_", " ")}</span>
                    <small>{event.event_id}</small>
                  </div>
                </div>
              ))}
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
          {brief && <><div className="section-h">Briefing</div><MarkdownBrief>{brief}</MarkdownBrief></>}
          {verdict && <div className={`verdict ${verdict.ok ? "ok" : "no"}`}>{verdict.text}</div>}
          {canAttest && (
            <div className="attestation">
              <div className="section-h">Human authority required</div>
              <p>The briefing is held. One authenticated pilot attestation may open one short-lived follower room.</p>
              <div className="row">
                <div><label>ETA (UTC)</label><input value={eta} placeholder="16:00Z"
                  onChange={(e) => setEta(e.target.value.toUpperCase())} /></div>
                <div><label>Grace (min)</label><input type="number" value={grace}
                  onChange={(e) => setGrace(+e.target.value)} /></div>
              </div>
              <button className="btn" onClick={attest} disabled={attesting || !etaValid}>
                {attesting ? "Attesting…" : "Attest & open follower room"}
              </button>
            </div>
          )}
          {mission?.status === "awaiting_attestation" && !canAttest && (
            <div className="attestation">
              <div className="section-h">Review required — handoff held</div>
              <p>The evidence gate did not approve this briefing. No attestation or follower room is available.</p>
            </div>
          )}
          {handoff && origin && (
            <div className="flight-follower-panel">
              <button className="follower-panel-toggle" type="button"
                aria-expanded={followerPanelOpen}
                onClick={() => setFollowerPanelOpen((value) => !value)}>
                <span>Flight follower</span><strong>{terminalLabel}</strong>
              </button>
              {followerPanelOpen && <FollowerRoom
                role="pilot"
                missionId={handoff.room_id}
                token={handoff.token}
                expiresAt={handoff.expires_at}
                inviteUrl={`${origin}/handoff/${encodeURIComponent(handoff.token)}`}
                onAcknowledged={setFollowingActive}
              />}
            </div>
          )}
          {error && <div className="verdict no">{error}</div>}
        </div>
      </div>
      <div className="right">
        <MapView ref={mapRef} theme={theme} />
        {!missionPanelOpen && (
          <button className="panel-reveal panel-reveal-left" type="button"
            aria-label="Show mission panel" aria-controls="mission-panel" aria-expanded={missionPanelOpen}
            onClick={() => setMissionPanelOpen(true)}>
            <b aria-hidden="true">›</b><span>MISSION</span>
          </button>
        )}
        {!proofPanelOpen && (
          <button className="panel-reveal panel-reveal-right" type="button"
            aria-label="Show proof panel" aria-controls="proof-panel" aria-expanded={proofPanelOpen}
            onClick={() => setProofPanelOpen(true)}>
            <b aria-hidden="true">‹</b><span>PROOF</span>
          </button>
        )}
        {planRevision && (
          <div className={`map-consequence ${mission?.status === "dispatched" || mission?.status === "accepted" ? "accepted" : ""}`} aria-label="Current landing plan consequence">
            <small>DETERMINISTIC CONSEQUENCE</small>
            <div><span>V1 EAST</span><b>REJECTED</b><i aria-hidden="true">→</i><span>V2 WEST</span><strong>{mission?.status === "dispatched" || mission?.status === "accepted" ? "ACCEPTED" : "PILOT REVIEW"}</strong></div>
          </div>
        )}
        <div className="legend">
          <div className="k"><span className="dot" style={{ background: "#f2b45a" }} />route</div>
          <div className="k"><span className="dot" style={{ background: "#35d0d6" }} />corridor ±10 NM</div>
          <div className="k"><span className="dot" style={{ background: "#e6eef7" }} />NOTAM on route</div>
          <div className="k"><span className="dot" style={{ background: "#ef6b6b" }} />FIR-wide NOTAM</div>
          <div className="k"><span className="dot" style={{ background: "#ff4f91" }} />changed source resurfaced</div>
          <div className="k"><span className="dot" style={{ background: "#57c98a" }} />source station</div>
        </div>
      </div>
      <ProofRail
        mission={mission}
        conditionCard={conditionCard}
        quarantine={quarantine}
        planRevision={planRevision}
        inference={inference}
        provenance={prov}
        briefingGate={briefingGate}
        dispatchGate={dispatchGate}
        degraded={degraded}
        dispatch={dispatch}
        followingActive={followingActive}
        onClose={() => setProofPanelOpen(false)}
        onReplayDispatch={mission?.status === "dispatched" ? replayHandoff : undefined}
        replayingDispatch={attesting}
      />
    </div>
  );
}
