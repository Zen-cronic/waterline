# Waterline

**A live flight briefing for station-less Canadian water destinations.**

A bush pilot flying a float plane to a remote lake may have no identifier-keyed destination observation to request. Waterline's current resolver supports a curated set of five Ontario water destinations with no destination weather station. It reduces the whole Flight Information Region's live NOTAM feed down to the hazards that actually touch the route, and it *infers* a weather read from the real observations that do exist nearby — never inventing a number, always showing its work.

> **The Twist:** every briefing tool is keyed to identifiers by construction. Waterline is keyed to **geometry** — a route corridor and an altitude band — so it works for a place that has no name in any aviation database.

---

## What it does, in one run

1. You enter a departure identifier (e.g. `CYYZ`), a destination lake with no identifier (e.g. *Lady Evelyn Lake*), and a cruise altitude.
2. Waterline pulls the **live NAV CANADA** NOTAM feed for the Flight Information Region — hundreds of NOTAMs — and reduces it, in PostGIS, to only the ones whose geometry intersects your route corridor and altitude band. *(Measured: 471 → 79 on a Toronto-FIR route — 83% of the FIR dropped as off-route.)*
3. For the station-less destination, it ranks the nearest real METAR stations and synthesizes a read with an **explicit confidence** that falls with distance and rises with agreement. The raw METAR travels with every inference.
4. Seven agents brief in sequence; a **Verifier** refuses any claim that doesn't trace to a source, a deterministic gate prevents rejected or structurally unsafe briefings from reaching DispatchAgent, and DispatchAgent files one human-gated flight-following notice.

Normal runs fetch current, reproducible government data. A local-only frozen capture may be used when developing offline, and its provenance is labelled explicitly. The exact live source request is one line and needs no key:

```
curl "https://plan.navcanada.ca/weather/api/alpha/?site=CZYZ&alpha=notam"
```

## The agent roster (strict separation of concerns)

| Agent | Job | Deterministic tool |
|-------|-----|--------------------|
| **RouteAgent** | resolve the request to coordinates | `resolve_route` |
| **IngestAgent** | fetch and load current FIR NOTAM + METAR inputs | `fetch_and_load_sources` |
| **CorridorAgent** | reduce the FIR set to the route | `filter_route_corridor` (PostGIS) |
| **WeatherAgent** | infer the station-less read | `infer_destination_weather` (PostGIS) |
| **BriefingComposer** | rank hazards for a low float flight, write the briefing | — |
| **Verifier** | refuse any claim not traceable to a source | — |
| **DispatchAgent** | file one human-gated flight-following notice | atomic Cloud SQL dispatch claim + SMTP/outbox |

The geometry is entirely in the tools; the agents orchestrate, rank, compose, and verify. **The model never receives a coordinate** — tools emit geometry to the map and return only scalars, so the LLM can narrate but cannot place a wrong point on the map.

## Architecture

```mermaid
flowchart LR
  UI[Next.js + MapLibre<br/>SSE, map builds itself] -- POST /brief --> SVC[FastAPI on Cloud Run]
  SVC --> ADK[ADK SequentialAgent<br/>7 named agents · Gemini 3.5+ Flash<br/>fallback chain 3.7→3.6→3.5]
  ADK -- tools --> PG[(Cloud SQL · PostGIS<br/>corridor filter, station ranking)]
  ADK -- live --> NAV[NAV CANADA alpha API<br/>NOTAM / METAR]
  ADK -- session state --> PG
```

- **Failure-tolerant routing:** the model layer tries `gemini-3.7-flash → 3.6 → 3.5`; a fresh model under launch load throws 503s, and the demo never stalls on one. Every model in the chain clears the required "Gemini 3.5 or newer" floor.
- **Crash recovery:** agent session state is checkpointed to Cloud SQL (`DatabaseSessionService`); a briefing killed mid-run resumes under the same session id.
- **Fail-closed dispatch:** a free-form Verifier rejection cannot fall through to DispatchAgent. A deterministic ADK callback independently checks required provenance, inference labelling, source station/distance, NOTAM indices, and the disclaimer before unlocking dispatch.
- **At-most-once notice:** Cloud SQL atomically claims an itinerary key before SMTP. Retry/resume and concurrent requests cannot send the same notice twice. An ambiguous SMTP failure intentionally remains claimed and requires operator reconciliation rather than an automatic retry that could duplicate a safety notice.
- **The Twist is spatial:** the NAV CANADA API refuses geometry queries (`bbox=`, `radius=`, `point=` all return `alpha.geomNone`; only `site=` works), so the route-corridor filter is genuinely our code, not theirs.

## Stack

- **Gemini 3.5+ Flash** (via the Google GenAI SDK), primary `gemini-3.7-flash`.
- **Google Agent Development Kit (ADK)** — `SequentialAgent` roster of seven `LlmAgent`s plus a deterministic pre-dispatch callback.
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
export VIRTUAL_ENV="$HOME/.pyenv/versions/.waterline"; export PATH="$VIRTUAL_ENV/bin:$PATH"
poetry install
poetry run uvicorn waterline.service:app --host 127.0.0.1 --port 8088

# 3. Frontend
cd ../web
pnpm install && pnpm dev             # http://localhost:3010
```

Data provenance: deployed NAV CANADA NOTAM/METAR are fetched live and are not bundled in the application image. Station coordinates come from the public-domain OurAirports dataset. Ignored local captures in `data/captures/` may support developer-only playback, but never cross the Cloud Build boundary.

## License

MIT.
