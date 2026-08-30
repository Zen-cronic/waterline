# Waterline verification guide

Run these checks from the repository root. They are ordered from deterministic local proof to deployed proof so a failed early gate never consumes cloud build or model-call budget.

## 1. Static and unit gates

```bash
git diff --check

# Use the project-specific environment explicitly; do not rely on Poetry's cached project selection.
"$HOME/.pyenv/versions/.waterline/bin/pytest" agent/tests -q

cd web
pnpm test
pnpm build
cd ..
```

If your project environment lives elsewhere, run that environment's `bin/pytest` directly. The suite covers live/fallback provenance contracts, strict condition-card validation, hostile-text quarantine, the state graph, owner-bound relay authentication, server-owned recipients, signed Twilio callbacks, deterministic gates, recovery, concurrency, and at-most-once handoff.

## 2. Packaging and non-root runtime gates

```bash
gcloud meta list-files-for-upload
(cd web && gcloud meta list-files-for-upload)

docker build -t waterline-agent:test .
docker build -t waterline-web:test ./web
docker run --rm --entrypoint id waterline-agent:test
docker run --rm --entrypoint id waterline-web:test
```

Inspect the source lists. They must include the airport reference, private condition-card manifest/image, agent modules, and web proof-rail modules while excluding `.env*`, `.git`, caches, local captures, outbox mail, tests from the runtime image, and Playwright artifacts.

For HTTP smoke checks, run each image on an unused local port and make GET-only health requests:

```bash
docker run --rm --name waterline-agent-smoke -p 18088:8080 waterline-agent:test
curl -fsS http://127.0.0.1:18088/health

docker run --rm --name waterline-web-smoke -p 13010:8080 -e PORT=8080 waterline-web:test
curl -fsS http://127.0.0.1:13010/
```

The containers must run as non-root users and return HTTP 200. Stop the foreground container before starting the next one.

## 3. Browser proof matrix

At desktop 1440×1000 and mobile 390×844, inspect these states:

| State | Required visible evidence |
|---|---|
| Awaiting attestation | Route/corridor/stations/NOTAM map; card extraction; hash-only quarantine; weather confidence/raw METAR; NOTAM and METAR receipts; v1 EAST rejected; v2 WEST pilot review; held Authority Map |
| Degraded | Durable rejected reason, recovery control, explicit “No mutation · no dispatch,” and held authority |
| Recovered | Same mission/session/trace, retained failure event, fresh proof snapshot, and one recovery transition |
| Provider accepted | Authenticated attestation, v2 WEST accepted, deterministic dispatch gate, redacted recipient, provider reference, and `PROVIDER ACCEPTED` (never mislabeled delivered) |
| Delivered | A valid signed callback advances the same receipt to `DELIVERED`; raw phone number and auth token remain absent |
| Replay | Expected HTTP 200 idempotent response, original receipt ID, “Replay suppressed,” and no second provider request |

Every viewport must have no horizontal overflow. Clean awaiting/dispatched flows must have zero console errors. MapLibre may emit WebGL performance warnings in headless rendering; record them as renderer warnings rather than application failures. The completed replay intentionally returns `200` with the original receipt and `duplicate_suppressed=true`; an ambiguous claimed-but-incomplete provider attempt remains a `409` reconciliation case.

### Repeat-flight memory and model-routing proof

Use the same authenticated browser owner for both flights. Flight 1 must show the
PostGIS reduction and then reach `dispatched`; its durable timeline must include
`terminal attested notam ack` with the number of acknowledgement rows written.
Immediately before starting flight 2, run the guarded mutation helper in a second
terminal so it changes a real database row after the new ingest and before the
corridor tool reads it:

```bash
DATABASE_URL="$DATABASE_URL" poetry -C agent run python \
  ../scripts/mutate_notam_for_memory_demo.py --owner-ref '<redacted owner_ref>'
```

The frozen reference beat is `471 → 79` geometry and then `79 → 23` owner
memory. Live FIR cardinality changes over time, so a deployed run must assert
the invariant rather than falsify the current feed: flight 2 ends at the
23-item safety floor, reports the exact number suppressed, names the changed
source, and says `Gemini reads 14 of 23; Gemma triaged the rest`. The latest
deployed proof on 2026-08-30 measured `103 → 23` with 80 unchanged
acknowledgements suppressed. The raw map layer must contain all 23 surfaced
records including the changed marker. A fresh live ingest restores the
synthetic mutation.

The guarded automation verifies outbox mode before it creates two missions and
one pilot attestation, starts Cloud SQL Auth Proxy without printing the database
secret, performs the real digest mutation, and retains a report/screenshot:

```bash
WATERLINE_MEMORY_PROOF_APPROVED=I_APPROVE_OUTBOX_MEMORY_PROOF \
  node web/scripts/verify-deployed-memory.mjs
```

Expected report invariants: `status=PASSED`, no browser errors, different first
and second mission IDs under the same owner, `→ 23`, one `resurfaced at full
weight` step, and `Gemini reads 14 of 23`.

Failure checks:

- Disable or break Vertex embeddings: recall must return zero memories and show all 79.
- Use a different authenticated owner: no acknowledgements may match.
- Return an incomplete or duplicate Gemma order: the adapter must append every omitted NOTAM; map cardinality cannot fall.
- Change either `raw` or `end_valid`: the NOTAM must resurface at full weight.

### Theme and gallery proof

`pnpm dev` serves the local UI at `http://localhost:3010`. Use the header toggle
to inspect the paired dark and light themes; changing themes must preserve any
streamed route/evidence layers. Generate safe no-mission theme frames and the
deterministic judge gallery with:

```bash
cd web
APP_URL=http://localhost:3010 node scripts/capture-theme-preview.mjs
APP_URL=http://localhost:3010 node scripts/capture-demo-gallery.mjs
```

The briefing must show rendered headings, emphasis, and lists—never literal
`##` or `**` markers—and no screenshot may contain the removed `HACKATHON DEMO
· PILOT REVIEW REQUIRED` footer.

## 4. Cloud foundation gate

These commands are read-only except for the verifier's API calls to inspect resources:

```bash
./deploy/verify_cloud_foundation.sh
gcloud run services list \
  --project=ata-2026-waterline \
  --region=us-central1 \
  --format='table(metadata.name,status.latestReadyRevisionName,status.url)'
```

Before iteration 7, the expected Cloud Run result is empty. That is a deliberate deployment gate, not a failure. The foundation verifier must prove 7/7 required APIs, the `waterline` Artifact Registry repository, two keyless service accounts and scoped roles, runnable PostgreSQL 16 Cloud SQL, the database/user, and three enabled secret versions with scoped access. It never prints secret payloads.

## 5. Preview deployment acceptance

After the operator authorizes iteration 7 and the two Cloud Run services are deployed, prove each item against the deployed revisions:

- Public web URL returns HTTP 200; anonymous direct invocation of the private agent is denied.
- The web service identity can invoke the private agent with the exact audience and signed command.
- A normal mission uses live NAV CANADA NOTAM/METAR receipts and a real Vertex Gemini typed visual receipt, or fails visibly—never silently falls back to a bundled capture.
- Refresh restores the same owner-bound mission from Cloud SQL.
- One recovery resumes the same mission/session; concurrent recovery cannot fork it.
- One server-allowlisted, operator-owned test destination receives at most one marked synthetic handoff only after authenticated attestation. Browser recipient fields fail closed.
- Twilio's initial response is labeled `PROVIDER ACCEPTED`; only a verified callback may produce `DELIVERED`.
- Replaying the same command returns HTTP 200 with the original receipt and sends no duplicate.
- Cloud Run revision/request logs, Cloud SQL rows, Vertex activity, Artifact Registry digest, and safe receipt/trace IDs are captured without secrets or contact PII.

Use the project-resolved Console links and exact deployment commands in the external operator file `/home/zin-kg/code/hackathons/allthingsagentic-2026/submission/waterline/google-cloud-setup-checklist.md`. Reusable operational lessons are recorded in `/home/zin-kg/code/hackathons/allthingsagentic-2026/submission/google-cloud-deployment-errors-and-workarounds.md`: quote gcloud format expressions, separate endpoint URLs from ID-token audiences, keep private service ingress distinct from IAM authorization, inspect build contexts, and use `/health` as the deployed proof path.
