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

The recorded deployed proof on 2026-08-31 measured acknowledgement at **309 ms**, follower→pilot at **224 ms**, and pilot→follower at **854 ms**. It verified one Cloud SQL receipt, one Firestore thread, two scoped members, one acknowledgement, history restoration, identical replay, and zero browser errors for mission `mission-11d9e8bf923c4e76aeb0`.

The verified 3:47 demo also shows that exact mission in Google Cloud Logs Explorer on private service `waterline-agent`, serving revision `waterline-agent-00008-62n`, including the successful Cloud Run attestation request. The product workflow remains one uninterrupted 1× execution; the subsequent Console proof and architecture are disclosed editorial inserts.

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

## Hero technology

| Technology | Load-bearing role | Authority boundary | Judge-visible proof |
|---|---|---|---|
| Google ADK on Vertex AI | Coordinates the eight-agent briefing fleet and typed tool calls | Agents read, rank, and propose; they never write mission authority | Named agent roster, structured briefing, mission/trace receipts |
| Gemini 3.7/3.6/3.5 Flash fallback chain | Extracts labelled condition evidence and composes sourced route reasoning despite transient model failures | Typed schemas, source digests, deterministic geometry, and verifier callbacks constrain output | Condition receipt, east-v1 rejection, west-v2 proposal, bounded inference |
| Cloud Run + IAM | Hosts the public Next.js edge and private FastAPI/ADK service | Exact HMAC relay plus IAM allows only the web service to invoke the private agent | Deployed `.run.app` origin and verified private invoker policy |
| Cloud SQL for PostgreSQL 16 + PostGIS + pgvector | Stores authoritative missions/events/attestation, performs route intersection, recalls owner-scoped evidence, and claims one room receipt | Append-only events and atomic SQL writes remain the source of truth | `ATTESTATION REQUIRED`, one receipt, identical replay, installed extension proof |
| Firebase Anonymous Auth + Cloud Firestore | Binds two temporary room members and delivers acknowledgement/chat in realtime | One-hour membership and Security Rules; messages cannot change route, attestation, or mission state | `HANDOFF READY → FOLLOWING ACTIVE`, bidirectional chat, reconnect |
| Secret Manager + Cloud Logging | Supplies keyless runtime configuration and exact-mission operational evidence | Reports retain secret names and request metadata, never secret values or raw capabilities | Revision/service-account proof and exact mission log entries |

## Bonus model usage

| Model | Usage | Safety property |
|---|---|---|
| `gemini-embedding-001` | Produces 768-dimensional destination embeddings for owner-scoped acknowledgement recall in pgvector | An embedding failure disables memory and resurfaces every candidate; it never suppresses a hazard |
| `google/gemma-4-26b-a4b-it-maas` | Advisory Vertex MaaS ranking of the complete NOTAM candidate set before the bounded Gemini reading budget | Gemma may reorder but cannot remove candidates; cardinality checks fail closed to deterministic order |

## Authority split

![Waterline deployed architecture](architecture/waterline-system.svg)

- **Cloud SQL is authoritative:** route decisions, attestation, mission status, append-only events, spatial filtering, owner memory, and the duplicate-suppression receipt.
- **Firestore is coordination only:** one-hour thread membership, a follower-only fixed acknowledgement, and plain-text messages. Firestore messages cannot change route, attestation, or mission state.
- **Possession is bounded:** the QR capability contains no contact detail or pilot identity, expires after one hour, and is stored in SQL only as SHA-256.
- **Rules fail closed:** unauthenticated, unbound, cross-mission, expired, spoofed-role, oversized, extra-field, update, and delete attempts are denied.

See [ARCHITECTURE.md](ARCHITECTURE.md), [TESTING.md](TESTING.md), and [DEPLOY.md](DEPLOY.md).

## Stack

- Google ADK, Vertex AI Gemini 3.7/3.6/3.5 Flash fallback chain, `gemini-embedding-001`, advisory `google/gemma-4-26b-a4b-it-maas`
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

## Submission links

| Artifact | Public URL | Prepared local copy |
|---|---|---|
| Demo video | `DEMO_VIDEO_URL_TBD` | `film/out/waterline-matilda-v6.mp4` |
| Blog post | `BLOG_POST_URL_TBD` | `../submission/waterline/blog.md` in the operator portfolio workspace |
| Social post | `SOCIAL_POST_URL_TBD` | `../submission/waterline/social.md` in the operator portfolio workspace |
| OSS contribution | `OSS_CONTRIBUTION_URL_TBD` | `../submission/waterline/adk-import-warning-upstream-draft.md` in the operator portfolio workspace |

These placeholders are intentionally explicit so no draft or local path can be mistaken for a published URL.

License: [MIT](LICENSE).
