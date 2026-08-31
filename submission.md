# Waterline — submission draft

## Tagline

Geometry-keyed flight briefings for remote water destinations, with a pilot-authorized realtime follower room.

## Inspiration

Remote lakes often have no destination identifier or weather station, but aviation briefing tools are identifier-keyed. Waterline starts from the route geometry instead: it narrows live FIR-wide hazards to the corridor, infers a bounded destination read from nearby observations, and keeps the pilot—not the model—in authority.

## What it does

Waterline runs an eight-agent ADK briefing fleet over live NAV CANADA inputs. PostGIS performs exact corridor intersection, Cloud SQL/pgvector recalls owner-scoped acknowledgements, Gemini extracts a typed synthetic condition card, and deterministic checks reject east-cove plan v1 before proposing west-cove v2 for pilot review.

The run stops at `ATTESTATION REQUIRED`. One authenticated pilot action atomically commits a Cloud SQL handoff receipt and displays a signed one-hour QR invitation. The responsible person scans it, acknowledges flight following, and exchanges bounded realtime text with the pilot through Firestore. The cockpit changes to `FOLLOWING ACTIVE · LIVE VIA FIRESTORE`. Replaying the command returns the same receipt, QR, token, and expiry.

Firestore is deliberately not authoritative: its messages cannot change route, attestation, or mission state.

## How we built it

- Google ADK and Vertex AI Gemini for the private briefing fleet
- `gemini-embedding-001` for owner-scoped destination recall and advisory `google/gemma-4-26b-a4b-it-maas` for cardinality-preserving NOTAM ranking
- Cloud Run public Next.js edge and private FastAPI agent
- Cloud SQL PostgreSQL/PostGIS/pgvector for spatial queries, durable missions, owner memory, and duplicate suppression
- Firebase Anonymous Auth plus a Cloud Run authorization bridge
- Cloud Firestore realtime listeners with strict Security Rules and one-hour TTL
- Secret Manager, keyless service accounts, Cloud Build, and Artifact Registry

## Challenges

The hard part was separating four kinds of power: models may read and propose; deterministic code validates and writes; the authenticated pilot authorizes; Firestore coordinates without acquiring route authority. The signed invitation also had to be reproducible after refresh without storing the raw capability, so Waterline persists only expiry and SHA-256 and reconstructs the exact token from durable mission state.

## Accomplishments

- Exact geometry-keyed NOTAM reduction instead of identifier lookup
- Typed visual extraction with digest-bound validation and hash-only hostile-text quarantine
- Owner-scoped source-digest memory with changed hazards resurfacing
- One durable handoff receipt under replay/concurrency
- One-hour, two-member Firestore room with fixed acknowledgement and bounded direct chat
- Emulator tests for unauthenticated, cross-room, expired, spoofed, oversized, extra-field, update, and delete denial
- Recorded deployed proof: 309 ms acknowledgement, 224 ms follower→pilot, 854 ms pilot→follower, history restoration, identical replay, zero browser errors

## What we learned

The safest architecture was also the clearest demo: Cloud SQL remains the source of truth, Firestore is labelled ephemeral coordination, and a prominent boundary states that messages cannot mutate aviation authority. Realtime UX does not require realtime authority.

## What's next

Expand only with cited, versioned water-aerodrome data and operator-reviewed escalation workflows without broadening model authority.

## Upstream contribution

Building Waterline surfaced an import-time Google ADK defect: importing the supported programmatic `LlmAgent` API emits a deprecation warning from ADK's own internal `BaseAgentConfig` subclass. We reported a clean reproduction across ADK 2.7.1, 2.8.0, and current upstream `main`, with a narrow regression-test proposal. The issue is open as of August 31, 2026: [`google/adk-python#6968`](https://github.com/google/adk-python/issues/6968).

## Links

- Live app: https://waterline-web-2hjaxuzova-uc.a.run.app
- Repository: https://github.com/Zen-cronic/waterline
- Devpost project: https://devpost.com/software/waterline
- Demo video: https://youtu.be/05H46M1ir-o
- Blog post: https://dev.to/zin_kg/the-destination-had-no-weather-station-so-i-stopped-asking-for-an-airport-code-3961
- Social post: `SOCIAL_POST_URL_TBD`
- OSS contribution: [`google/adk-python#6968`](https://github.com/google/adk-python/issues/6968) — open

## Judge testing

1. Open the live app and brief CYYZ → Lady Evelyn Lake.
2. Inspect the east-v1 rejection, west-v2 proposal, sources, and held gate.
3. Select **Attest & open follower room** once.
4. Open the QR/link in a second browser or phone and acknowledge.
5. Exchange one message each way; observe `FOLLOWING ACTIVE · LIVE VIA FIRESTORE`.
6. Replay the handoff and confirm the original receipt/link returns.
