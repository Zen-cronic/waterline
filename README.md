# Waterline

**A live flight briefing and flight-following handoff for Canadian water destinations without a weather station.**

**Public preview:** [waterline-web-2hjaxuzova-uc.a.run.app](https://waterline-web-2hjaxuzova-uc.a.run.app) — deployed in `us-central1` from `0852d4f` (`waterline-agent-00008-62n`, `waterline-web-00008-ckb`). The public Next.js service reaches a private ADK agent through Cloud Run IAM and an exact HMAC-signed relay.

Waterline is keyed to geometry instead of a destination aerodrome identifier. It fetches live NAV CANADA inputs, uses PostGIS to reduce the FIR-wide NOTAM set to a route corridor and altitude band, and infers a bounded destination weather read from nearby real METAR observations. Every inference retains its source and confidence.

## One run

1. The pilot enters a departure, one of five curated Ontario water destinations, and cruise altitude.
2. Gemini proposes typed fields from Waterline's prepared condition card. Deterministic validation accepts the east-cove obstruction and quarantines embedded hostile text by hash.
3. PostGIS filters live route hazards. Owner-scoped Cloud SQL/pgvector memory suppresses only unchanged, still-valid acknowledgements; changed source digests resurface.
4. The ADK fleet rejects east-cove plan v1, proposes west-cove v2, and stops at `ATTESTATION REQUIRED`.
5. The authenticated pilot selects **Attest & open follower room**. Cloud SQL atomically claims one receipt and returns a deterministic, signed one-hour invitation.
6. A responsible person scans the QR, signs in anonymously with Firebase Auth, and acknowledges flight following. Both browsers then exchange bounded text directly through Firestore Security Rules.
7. The cockpit changes to `FOLLOWING ACTIVE · LIVE VIA FIRESTORE`. Replaying the handoff returns the identical receipt, QR, token, and expiry.

The deployed proof on 2026-08-30 measured acknowledgement at **206 ms**, follower→pilot at **113 ms**, and pilot→follower at **316 ms**. It verified one Cloud SQL receipt, one Firestore thread, two scoped members, one acknowledgement, history restoration, identical replay, and zero browser errors.

## Agent roster

| Agent | Responsibility | Deterministic tool or boundary |
|---|---|---|
| RouteAgent | Resolve route geometry | `resolve_route` |
| IngestAgent | Load live NOTAM and METAR inputs | `fetch_and_load_sources` |
| CorridorAgent | Reduce the FIR set to the route | `filter_route_corridor` / PostGIS |
| RecallAgent | Recall exact owner acknowledgements and rank | Cloud SQL vector + digest checks + advisory Gemma |
| WeatherAgent | Infer the station-less read | nearby METAR weighting |
| BriefingComposer | Compose the sourced briefing | read/propose only |
| Verifier | Refuse unsupported claims | semantic review + deterministic callback |
| FollowingAgent | Open the attested follower room | atomic SQL claim + signed capability |

Models never write mission state. The authenticated pilot is the sole human authorizer; deterministic code owns the state graph and SQL writes.

## Authority split

![Waterline deployed architecture](architecture/waterline-system.svg)

- **Cloud SQL is authoritative:** route decisions, attestation, mission status, append-only events, spatial filtering, owner memory, and the duplicate-suppression receipt.
- **Firestore is coordination only:** one-hour thread membership, a follower-only fixed acknowledgement, and plain-text messages. Firestore messages cannot change route, attestation, or mission state.
- **Possession is bounded:** the QR capability contains no contact detail or pilot identity, expires after one hour, and is stored in SQL only as SHA-256.
- **Rules fail closed:** unauthenticated, unbound, cross-mission, expired, spoofed-role, oversized, extra-field, update, and delete attempts are denied.

See [ARCHITECTURE.md](ARCHITECTURE.md), [TESTING.md](TESTING.md), and [DEPLOY.md](DEPLOY.md).

## Stack

- Google ADK, Vertex AI Gemini 3.5+ fallback chain, `gemini-embedding-001`, advisory Gemma 4
- Public Next.js 16 and private FastAPI services on Cloud Run
- Cloud SQL for PostgreSQL 16 with PostGIS and pgvector
- Firebase Anonymous Authentication and Cloud Firestore realtime listeners
- React 19, MapLibre GL, Firebase client/Admin SDKs

## Local development

```bash
docker compose up -d

cd agent
export GOOGLE_API_KEY=...
export DATABASE_URL=postgresql://waterline:waterline@localhost:5455/waterline
export WATERLINE_HANDOFF_SECRET='at-least-32-bytes-for-local-development'
poetry install
poetry run uvicorn waterline.service:app --host 127.0.0.1 --port 8088

cd ../web
export FIREBASE_API_KEY=...
export FIREBASE_AUTH_DOMAIN=...
export FIREBASE_PROJECT_ID=...
export FIREBASE_MESSAGING_SENDER_ID=...
export FIREBASE_APP_ID=...
export WATERLINE_HANDOFF_SECRET='the-same-local-value'
pnpm install
pnpm dev
```

Run `pnpm test:rules` from `web/` for the Firestore emulator suite. Firebase public web configuration identifies the project; authorization is enforced by Firebase Auth, the server bridge, and Security Rules. No service-account key is created.

Data provenance: deployed NOTAM/METAR data is fetched live and is not bundled. Station coordinates come from the public-domain OurAirports dataset. The Lady Evelyn condition card is a tracked Waterline-created synthetic artifact with a SHA-256 manifest.

License: [MIT](LICENSE).
