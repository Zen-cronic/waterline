# Waterline — build checkpoint & engineering handoff

_Last updated: 2026-08-23. Read this first if you're picking up the build._

## Status: **Cloud-deploy preflight repaired and locally verified.** Not yet deployed.

Waterline is a live flight briefing for station-less seaplane bases (see `README.md` for the pitch). The full stack runs and has been verified end to end locally. What remains is deployment, the Stage Three bonus, and recording the demo — all listed under **Next steps**.

## Architecture (what's built)

- **Agent** (`agent/waterline/`, Python 3.12, ADK 2.7 + Gemini Flash): a `SequentialAgent` roster of **7 named agents** — RouteAgent → IngestAgent → CorridorAgent → WeatherAgent → BriefingComposer → **Verifier** → **DispatchAgent**. Deterministic geometry lives in the tools (PostGIS); the model never receives coordinates (it triggers and narrates, tools emit geometry + return scalars).
  - `notam.py` — Q-line → geometry parser (pure, 100% parse rate on live data).
  - `db.py` — PostGIS: corridor filter (`ST_Buffer`/`ST_Intersects` on geography), nearest-station ranking, route geometry export.
  - `metar.py` — METAR decode + the station-less **inference** (rank nearest stations, confidence falls with distance / rises with agreement; never invents a value).
  - `navcanada.py` — live NAV CANADA fetch (falls back to `data/captures/` when offline).
  - `emit.py` — SSE layer/step/panel emission (the "map builds itself" mechanic; async-queue + contextvar).
  - `agents/model.py` — **FallbackGemini**: model chain `3.7→3.6→3.5 flash` with retry+backoff (see Gotchas).
  - `dispatch.py` + `tools/dispatch_tools.py` — the **real-world loop**: files a flight itinerary + sends a flight-following email (SMTP if configured, else a local outbox `.eml`). Human-gated (only fires with a responsible-person email).
  - `verification.py` — deterministic fail-closed gate between Verifier and DispatchAgent. It requires both semantic approval and machine-checkable provenance invariants.
  - `dispatch_receipts` — atomic Cloud SQL claim makes flight-following notices at-most-once across retry/resume; ambiguous SMTP failures require operator reconciliation rather than risking a duplicate.
  - `service.py` — FastAPI, `POST /brief` (SSE), `GET /health`.
- **Frontend** (`web/`, Next.js 16 + React 19 + MapLibre): streams `/brief`, paints corridor/route/NOTAM/station layers as they arrive, shows the agent-roster feed, briefing, Verifier verdict, and dispatch confirmation. Flight-following email field wired.
- **DB** (`db/schema.sql`, `docker-compose.yml`): PostGIS (`postgis/postgis:16-3.4`).

## How to run locally

```bash
# from waterline/
docker compose up -d                     # PostGIS on host port 5455
# agent
cd agent
export VIRTUAL_ENV="$HOME/.pyenv/versions/.waterline"; export PATH="$VIRTUAL_ENV/bin:$PATH"  # or: pyenv activate .waterline
set -a; source .env; set +a              # GOOGLE_API_KEY, DATABASE_URL, WATERLINE_MODEL_CHAIN
poetry install
poetry run uvicorn waterline.service:app --host 127.0.0.1 --port 8088
# frontend (new shell)
cd ../web && NEXT_PUBLIC_AGENT_URL=http://127.0.0.1:8088 pnpm dev   # http://localhost:3010
```

First run loads data automatically via the IngestAgent (live NAV CANADA). To pre-seed manually, the captures in `data/captures/` and the reference `data/reference/airports_ca.csv` are used. **`data/reference/airports_ca.csv` is required at runtime and IS committed**; `data/captures/` and `data/outbox/` are gitignored (regenerable).

## Verified (measured, live)

- Q-line geometry parse: **470/470 = 100%** on the live Toronto FIR (`site=CZYZ`).
- Corridor filter: **471 → ~79 on-route (~83% of the FIR dropped)**, entirely in PostGIS.
- Station-less inference: Lady Evelyn Lake → nearest station CYXR **27.8 NM**, confidence ~0.14 (honestly low).
- Full 7-agent pipeline runs green over HTTP; Verifier enforces "inferred, not measured"; DispatchAgent writes a real `.eml`.
- Frontend: `pnpm build` passes (type-check + compile); serves 200.
- Focused safety/packaging suite: safe METAR fractions, real ADK callback halt, ToolContext session identity, retry/resume duplicate suppression, intentional SMTP-failure claim behavior, and both Cloud Run contracts.
- Backend image: locked Poetry dependency install, non-root runtime, `0.0.0.0:$PORT`, tracked references + private frozen captures in the build context.
- Frontend image: Next.js standalone output, non-root runtime, `0.0.0.0:$PORT`, explicit build-time agent URL through `web/cloudbuild.yaml`.

## Next steps (in priority order)

1. **Deploy to Cloud Run + Cloud SQL** — the rubric requires on-camera GCP proof. Full guide in `DEPLOY.md`. Needs `gcloud` + a GCP project. Set `WATERLINE_SESSION_DB` (SQLAlchemy URL) to enable durable sessions (crash-resume beat).
2. **Real SMTP** for the flight-following email (set `WATERLINE_SMTP_*`) so the demo shows a real inbox delivery.
3. **Stage Three (+1.0)**: wire an extra Google model (Gemma/Veo/Lyria) + publish content with the `#AllThingsAgenticHackathon` attestation.
4. **Record the demo** per `docs/demo-run-of-show.md` (gitignored; 4-min cap, rubric-weighted).

## Gotchas (real, learned the hard way)

- **Gemini Flash 503-flaps under launch load.** All three models can be down for a few seconds at once. `FallbackGemini` sweeps the chain up to 3× with 2s/4s backoff — keep it; it demonstrably rode out a live storm. Every model in the chain must stay ≥ `gemini-3.5-flash` (the hackathon floor). `gemini-3.5-pro` does NOT exist — Flash line only.
- **Headless chromium won't launch in the sandbox** — the Playwright smoke (`web/scripts/smoke.mjs`) can't run here. Verify the frontend with `pnpm build` + a `curl` of `/` instead. It'll work on a normal machine.
- **`docker compose` project name = folder basename.** This folder was renamed to `waterline` specifically so it stops colliding with other `placeholder-*` dirs. If you `docker compose up`, expect a fresh `waterline-*` project/volume; reload data on first up.
- Strategy/rationale (rubric, competitor, demo script) lives in `docs/` (gitignored) and in the main project's `state.md` (in the `hackathon-agent` repo).
- The GitHub remote exists, but this repair branch is local-only. Do not push without the operator's go-ahead.
