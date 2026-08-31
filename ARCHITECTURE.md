# Waterline architecture

![Waterline deployed bounded-authority architecture](architecture/waterline-system.svg)

The 1920×1080 diagram shows four deployed stages and one hard authority boundary.

## Four stages

| Stage | Deployed components | Authority |
|---|---|---|
| Pilot and follower | Pilot cockpit, signed QR invitation, phone handoff page | The authenticated pilot alone may attest; possession grants only the one-hour room |
| Protected Cloud Run edge | Public Next.js application, exact signed mission relay, chat authorization bridge, Firebase Anonymous Auth | Normalizes commands and binds UIDs; cannot decide a route |
| Intelligence and authority | Private ADK/Gemini fleet, deterministic verifier, pilot gate, Cloud SQL/PostGIS/pgvector | Models read/propose; deterministic code and the pilot write authoritative state |
| Realtime consequence | Firestore thread, two scoped members, acknowledgement and bidirectional chat | Ephemeral coordination only; no route, attestation, or mission mutation |

## Request and handoff sequence

1. The public web service issues a tamper-evident HttpOnly pilot session and relays only allowlisted commands.
2. Cloud Run IAM authenticates `waterline-web` to the private agent. HMAC binds the opaque actor, timestamp, method, normalized path, and normalized body.
3. The fleet reads live source data and proposes a briefing. PostGIS performs exact corridor intersection; Cloud SQL/pgvector supplies owner-scoped recall.
4. Deterministic checks validate evidence, source references, the plan revision, and the owner-bound pilot attestation.
5. Cloud SQL commits one handoff receipt with `channel=firestore`, `provider_reference=<mission-id>`, `provider_status=room_ready`, persisted expiry, and token SHA-256. The raw capability is reconstructed, never stored.
6. Pilot and follower sign in anonymously, exchange Firebase ID tokens for server-created membership, then subscribe and write messages directly under Firestore Rules.
7. A fixed acknowledgement changes the pilot presentation to `FOLLOWING ACTIVE`; it does not mutate the SQL mission, whose compatibility terminal status remains `dispatched`.

## Firestore boundary

```text
handoff_threads/{missionId}
  members/{firebaseUid}
  messages/{messageId}
```

- One thread per mission; one-hour membership and TTL.
- Latest 50 messages in the UI; plain text up to 500 characters.
- Only `text` and the fixed `Following acknowledged` acknowledgement.
- Clients cannot create or modify thread/member documents.
- Message sender UID and role must match the active server-created membership.
- `createdAt` must equal request time; updates, deletes, attachments, cross-mission reads, and expired access are denied.

TTL performs eventual cleanup, while rules stop access immediately at expiry.

## Failure behavior

- Ambiguous evidence stops before the agent pipeline and requests review.
- A model or source failure appends a durable rejected event; the same owner may resume the same session.
- A Firestore or authorization failure shows `FOLLOWER ROOM UNAVAILABLE`. The attestation and SQL receipt remain durable, and no automatic authority transition occurs.
- Replay reconstructs the original capability from owner state plus the persisted expiry and verifies it against the stored SHA-256 before returning it.

## Deployed proof

Recorded mission `mission-11d9e8bf923c4e76aeb0` on 2026-08-31 verified:

- one Cloud SQL receipt row and one distinct claim;
- one Firestore thread, `pilot` and `follower` members, and one acknowledgement;
- 309 ms acknowledgement, 224 ms follower→pilot, and 854 ms pilot→follower propagation;
- reconnect history restoration and identical receipt/link/expiry replay;
- zero browser errors.

## Rubric traceability

| Axis | Architecture proof | Judge-visible proof |
|---|---|---|
| Innovation | Geometry-keyed station-less briefing plus a possession-bound realtime follower room | East v1 rejection, west v2 proposal, QR handoff, live acknowledgement |
| Architecture | Private agent, exact signed relay, deterministic writer, Cloud SQL authority, scoped Firestore coordination | Authority map, durable receipt, Security Rules, explicit hard boundary |
| Demo | One causal pilot→phone→pilot loop with duplicate-safe replay | `ATTESTATION REQUIRED → HANDOFF READY → FOLLOWING ACTIVE` |
| Google Cloud | Cloud Run, Vertex AI, Cloud SQL/PostGIS/pgvector, Firebase Auth, Firestore, Secret Manager, Artifact Registry | Live revisions, source/model receipts, realtime messages, SQL and Firestore counts |

Artifacts: [`architecture/waterline-system.svg`](architecture/waterline-system.svg) is the source; `architecture/waterline-system.png` is the inspected 1920×1080 submission raster.
