# Waterline verification guide

Run from the repository root unless noted.

## Local gates

```bash
git diff --check
poetry -C agent run pytest

cd web
pnpm test
pnpm test:rules
pnpm build
node --check scripts/verify-deployed-firestore.mjs
```

Expected current results: 54 backend tests, 16 web tests, 6 Firestore emulator tests, and a successful Next.js production build.

The emulator suite proves:

- unauthenticated and unbound denial;
- pilot/follower same-room access and cross-mission denial;
- expired membership denial;
- UID and role spoofing denial;
- client thread/member creation denial;
- required message schema, 500-character bound, and query limit;
- follower-only fixed acknowledgement success and pilot acknowledgement denial;
- message update and delete denial.

Backend/web tests cover altered and expired capabilities, owner mismatch, role conflict, identical replay, reconstructed invitation, exact relay policy, and the atomic compatibility receipt contract.

## Browser matrix

| State | Required evidence |
|---|---|
| `ATTESTATION REQUIRED` | live map/source proof, east v1 rejected, west v2 proposed, held handoff gate |
| `HANDOFF READY` | one receipt, signed QR/link, one-hour expiry, waiting-for-follower state |
| follower joined | phone route summary and acknowledgement control; pilot shows joined/waiting-for-ack |
| `FOLLOWING ACTIVE` | fixed acknowledgement visible on both surfaces and `LIVE VIA FIRESTORE` |
| bidirectional chat | follower message reaches laptop; pilot reply reaches phone within two seconds |
| reconnect | latest history returns after phone reload |
| replay | original receipt, invitation, token, expiry, and one existing Firestore thread |
| unavailable | `FOLLOWER ROOM UNAVAILABLE`; no SQL authority or mission transition |

Desktop and 390×844 phone layouts must have no horizontal overflow or browser errors.

## Cloud foundation

```bash
./deploy/verify_cloud_foundation.sh
gcloud firestore fields ttls list \
  --project=ata-2026-waterline \
  --database='(default)'
```

The foundation verifier requires 10 APIs, keyless runtime identities, private/public Cloud Run boundaries, runnable Cloud SQL, scoped secrets, and `roles/datastore.user` for the web service. TTL must be `ACTIVE` for `handoff_threads`, `members`, and `messages` on `expiresAt`.

## Deployed acceptance

```bash
cd web
WATERLINE_FIRESTORE_PROOF_APPROVED=I_APPROVE_FIRESTORE_FLIGHT_FOLLOWING_PROOF \
  pnpm verify:deployed-firestore
```

The successful 2026-08-30 report is retained under ignored `.playwright-mcp/deployed-firestore/` and recorded in [ARCHITECTURE.md](ARCHITECTURE.md). It passed with:

- mission `mission-d9fa83f7997a47fe91f1`;
- one SQL receipt and one Firestore thread;
- exactly two scoped members and one acknowledgement;
- propagation at 206 ms / 113 ms / 316 ms;
- reconnect history and identical replay;
- zero browser errors.

Do not record raw capability URLs, secret payloads, service identity tokens, or contact data in reports or screenshots.
