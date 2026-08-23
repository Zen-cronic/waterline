"use client";
import { useRef, useState } from "react";
import { MapView, type MapHandle } from "@/components/MapView";

const AGENT = process.env.NEXT_PUBLIC_AGENT_URL || "http://127.0.0.1:8088";
type Step = { agent: string; kind: string; detail: string };
type DispatchResult = {
  to?: string;
  channel?: string;
  sent?: boolean;
  duplicate_suppressed?: boolean;
};
const LAKES = ["Lady Evelyn Lake", "Lake Temagami", "Biscotasi Lake", "Smoothwater Lake"];

export default function Page() {
  const mapRef = useRef<MapHandle>(null);
  const [dep, setDep] = useState("CYYZ");
  const [dst, setDst] = useState("Lady Evelyn Lake");
  const [alt, setAlt] = useState(3500);
  const [email, setEmail] = useState("");
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);
  const [brief, setBrief] = useState("");
  const [verdict, setVerdict] = useState<{ ok: boolean; text: string } | null>(null);
  const [prov, setProv] = useState<string | null>(null);
  const [dispatch, setDispatch] = useState<DispatchResult | null>(null);

  async function run() {
    setRunning(true); setSteps([]); setBrief(""); setVerdict(null); setProv(null); setDispatch(null);
    mapRef.current?.reset();
    const text = `Brief my flight from ${dep} to ${dst} at ${alt} feet this afternoon.`;
    const res = await fetch(`${AGENT}/brief`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, session_id: `web-${Date.now()}`,
        responsible_email: email || null, eta: "this afternoon", grace_min: 60 }),
    });
    const reader = res.body!.getReader();
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
        else if (ev.type === "panel" && ev.key === "provenance") setProv(ev.value?.source ?? null);
        else if (ev.type === "panel" && ev.key === "dispatch") setDispatch(ev.value);
        else if (ev.type === "agent" && ev.final && ev.author === "BriefingComposer") setBrief(ev.text);
        else if (ev.type === "agent" && ev.final && ev.author === "Verifier")
          setVerdict({ ok: ev.text.trim().toUpperCase().startsWith("APPROVED"), text: ev.text });
      }
    }
    setRunning(false);
  }

  return (
    <div className="app">
      <div className="left">
        <div className="brand">
          <h1><span>Waterline</span></h1>
          <p>A live flight briefing for the 446 seaplane bases with no identifier — no station.</p>
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
            <div>
              <label>Flight-following (optional)</label>
              <input placeholder="responsible person's email" value={email}
                onChange={(e) => setEmail(e.target.value)} />
            </div>
          </div>
          <button className="btn" onClick={run} disabled={running}>
            {running ? "Briefing…" : "Brief this flight"}
          </button>
        </div>
        <div className="feed">
          <div className="section-h">Agent roster</div>
          {steps.length === 0 && !running && <div className="prov">Seven agents brief in sequence.</div>}
          {steps.map((s, i) => (
            <div className="step" key={i}>
              <span className={`who ${s.agent}`}>{s.agent}</span>
              <span>{s.detail}<span className="badge">{s.kind}</span></span>
            </div>
          ))}
          {brief && <><div className="section-h">Briefing</div><div className="brief">{brief}</div></>}
          {verdict && <div className={`verdict ${verdict.ok ? "ok" : "no"}`}>{verdict.text}</div>}
          {dispatch && (
            <div className="verdict ok" style={{ background: "rgba(53,208,214,.12)", color: "#35d0d6", borderColor: "rgba(53,208,214,.3)" }}>
              {dispatch.duplicate_suppressed
                ? "✈ Itinerary already filed · retry sent no duplicate notice"
                : `✈ Itinerary filed · flight-following notice sent to ${dispatch.to} via ${dispatch.channel}`}
            </div>
          )}
          {prov && <div className="prov">source: {prov}</div>}
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
