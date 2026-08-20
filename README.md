# Waterline

**A live flight briefing for the 446 Canadian seaplane bases that don't have one.**

A bush pilot flying a float plane to a remote lake gets no briefing from any tool on the market. Those tools are keyed to *identifiers* — an airport code that maps to a weather station. **446 of Canada's 449 registered seaplane bases have no identifier, so no station, so every briefing tool goes blank exactly where the pilot is going.** Waterline briefs the lake anyway: it reduces the whole Flight Information Region's live NOTAM feed down to the hazards that actually touch your route, and it *infers* a weather read for a station-less destination from the real observations that do exist nearby — never inventing a number, always showing its work.

> **The Twist:** every briefing tool is keyed to identifiers by construction. Waterline is keyed to **geometry** — a route corridor and an altitude band — so it works for a place that has no name in any aviation database.

---

## What it does, in one run

1. You enter a departure identifier (e.g. `CYYZ`), a destination lake with no identifier (e.g. *Lady Evelyn Lake*), and a cruise altitude.
2. Waterline pulls the **live NAV CANADA** NOTAM feed for the Flight Information Region — hundreds of NOTAMs — and reduces it, in PostGIS, to only the ones whose geometry intersects your route corridor and altitude band. *(Measured: 471 → 79 on a Toronto-FIR route — 83% of the FIR dropped as off-route.)*
3. For the station-less destination, it ranks the nearest real METAR stations and synthesizes a read with an **explicit confidence** that falls with distance and rises with agreement. The raw METAR travels with every inference.
4. Six agents brief in sequence; a **Verifier** refuses any claim that doesn't trace to a source and enforces that the destination read is labelled *inferred, not measured*.

Everything runs on live, reproducible government data. The exact source request is one line and needs no key:

```
curl "https://plan.navcanada.ca/weather/api/alpha/?site=CZYZ&alpha=notam"
```

## The agent roster (strict separation of concerns)

| Agent | Job | Deterministic tool |
|-------|-----|--------------------|
| **RouteAgent** | resolve the request to coordinates | `resolve_route` |
| **IngestAgent** | pull the live FIR NOTAM feed | `fetch_and_load_notams` |
| **CorridorAgent** | reduce the FIR set to the route | `filter_route_corridor` (PostGIS) |
| **WeatherAgent** | infer the station-less read | `infer_destination_weather` (PostGIS) |
| **BriefingComposer** | rank hazards for a low float flight, write the briefing | — |
| **Verifier** | refuse any claim not traceable to a source | — |

The geometry is entirely in the tools; the agents orchestrate, rank, compose, and verify. **The model never receives a coordinate** — tools emit geometry to the map and return only scalars, so the LLM can narrate but cannot place a wrong point on the map.

## Architecture

```mermaid
flowchart LR
  UI[Next.js + MapLibre<br/>SSE, map builds itself] -- POST /brief --> SVC[FastAPI on Cloud Run]
  SVC --> ADK[ADK SequentialAgent<br/>6 named agents · Gemini 3.5+ Flash<br/>fallback chain 3.7→3.6→3.5]
  ADK -- tools --> PG[(Cloud SQL · PostGIS<br/>corridor filter, station ranking)]
  ADK -- live --> NAV[NAV CANADA alpha API<br/>NOTAM / METAR]
  ADK -- session state --> PG
```

- **Failure-tolerant routing:** the model layer tries `gemini-3.7-flash → 3.6 → 3.5`; a fresh model under launch load throws 503s, and the demo never stalls on one. Every model in the chain clears the required "Gemini 3.5 or newer" floor.
- **Crash recovery:** agent session state is checkpointed to Cloud SQL (`DatabaseSessionService`); a briefing killed mid-run resumes under the same session id.
- **The Twist is spatial:** the NAV CANADA API refuses geometry queries (`bbox=`, `radius=`, `point=` all return `alpha.geomNone`; only `site=` works), so the route-corridor filter is genuinely our code, not theirs.

## Stack

- **Gemini 3.5+ Flash** (via the Google GenAI SDK), primary `gemini-3.7-flash`.
- **Google Agent Development Kit (ADK)** — `SequentialAgent` roster of six `LlmAgent`s.
- **Google Cloud Run** (agent service) + **Cloud SQL for PostgreSQL / PostGIS** (spatial filtering + durable sessions).
- **Next.js 16 / React 19 / MapLibre GL** frontend, streaming Server-Sent Events.

## Run it locally

```bash
# 1. Postgres + PostGIS
docker compose up -d

# 2. Agent service (Python 3.12)
cd agent
export GOOGLE_API_KEY=...            # a Gemini API key
export DATABASE_URL=postgresql://waterline:waterline@localhost:5455/waterline
uvicorn waterline.service:app --port 8088

# 3. Frontend
cd ../web
pnpm install && pnpm dev             # http://localhost:3010
```

Data provenance: NAV CANADA NOTAM/METAR are live and consumed, never redistributed. Station coordinates come from the public-domain OurAirports dataset. A frozen capture in `data/captures/` gives the demo deterministic playback of a real pull.

## License

MIT.
