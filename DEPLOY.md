# Waterline deployment

Waterline deploys a public Next.js service and private FastAPI/ADK service to Cloud Run in `us-central1`. Cloud SQL is authoritative; Firebase Auth and Firestore provide one-hour realtime coordination.

## Prerequisites

- APIs: Cloud Run, Cloud Build, Artifact Registry, Vertex AI, Cloud SQL Admin, Secret Manager, IAM, Firestore, Identity Toolkit, and Firebase Rules.
- Firestore `(default)`: Standard, Native mode, `us-central1`.
- Firebase web app `waterline-web`; Anonymous authentication enabled; `localhost` and `waterline-web-2hjaxuzova-uc.a.run.app` authorized.
- Keyless service accounts `waterline-runtime` and `waterline-web`.
- `waterline-web` has `roles/datastore.user`; both service accounts can access `waterline-handoff-secret`.
- No service-account key is created.

Only `waterline-handoff-secret` is added for the follower-room feature. Firebase web configuration is passed as public Cloud Run environment values.

## Deploy the committed tree

The deployment script refuses dirty runtime paths and tags both images with the current 12-character commit.

```bash
WL_FIREBASE_API_KEY='public Firebase web API key' \
WL_FIREBASE_MESSAGING_SENDER_ID='Firebase sender id' \
WL_FIREBASE_APP_ID='Firebase web app id' \
  ./deploy/deploy_preview.sh
```

The script:

1. builds and deploys the private agent with Cloud SQL and the handoff secret;
2. grants only `waterline-web` Cloud Run invocation;
3. builds the public web image;
4. releases the tracked Firestore Rules;
5. enables `expiresAt` TTL for threads, members, and messages;
6. deploys the web service with Firebase public configuration and Application Default Credentials.

`WATERLINE_AGENT_URL` and `WATERLINE_AGENT_AUDIENCE` remain separate. The browser never receives either relay secret or a Google service credential.

## Schema

Run `deploy/provision_cloud_sql.sh` when creating or migrating the database. `db/schema.sql` adds `handoff_expires_at` and `handoff_token_sha256` to the compatibility receipt table. It does not store the raw capability.

## Verification

```bash
./deploy/verify_cloud_foundation.sh

cd web
WATERLINE_FIRESTORE_PROOF_APPROVED=I_APPROVE_FIRESTORE_FLIGHT_FOLLOWING_PROOF \
  pnpm verify:deployed-firestore
```

The live verifier creates one synthetic mission and attestation, uses separate pilot/follower browser contexts, asserts sub-two-second acknowledgement and messages, reloads history, replays the handoff, and directly checks one Firestore thread with two roles and one acknowledgement. Query Cloud SQL separately for the matching one-row receipt without printing the database credential.

## Failure and rollback

- Rules are released before the web revision receives traffic.
- If the chat bridge or Firestore is unavailable, the UI shows `FOLLOWER ROOM UNAVAILABLE`; the SQL mission and attestation remain durable.
- Revert Cloud Run traffic to the prior immutable web/agent revisions if a rollout fails. Firestore access remains fail-closed under the released rules.
- Never delete or rotate shared SQL/session/relay secrets as part of a routine rollback.

Current verified deployment: commit `0852d4f`, agent `waterline-agent-00008-62n`, web `waterline-web-00008-ckb`.
