# Waterline — Devpost submission form

Paste-ready draft for the All Things Agentic Hackathon. Every unresolved field is marked
`PENDING OPERATOR`; do not replace those markers until the linked artifact or choice is verified.

Authenticated Devpost state checked 2026-08-28: project `1387756` exists at
`https://devpost.com/software/1387756`, but remains an **Untitled** `submission_pre_draft` with no
published slug, description, tagline, video URL, or final submission timestamp. Updating this file
does not update Devpost.

## Project name

**Waterline**

## Elevator pitch / tagline

Primary (145 characters):

> Seven agents brief station-less water destinations, reject a poisoned route, and let only an authenticated pilot trigger one replay-safe handoff.

Alternates:

> A geometry-first aviation agent fleet briefs lakes without weather stations while deterministic policy and one pilot keep the dispatch key.

> Live aviation evidence in; a corrected water route and one at-most-once flight-following handoff out—without giving agents authority.

## Project Story — About the project

### Inspiration

Float pilots often fly to water destinations with no airport identifier and no destination weather
station. Identifier-keyed briefing tools lose their organizing key precisely where the trip ends.
We wanted agents to do the evidence-heavy work without quietly becoming the operational authority.

### What it does

Waterline briefs a curated set of five station-less Ontario water destinations by geometry rather
than by destination code. For one live Lady Evelyn Lake mission, it ingests current NAV CANADA
NOTAM and METAR data, filters the Toronto FIR to the route corridor in PostGIS, and infers a
destination read from nearby real stations with explicit confidence and raw-source provenance. In
the deployed proof, the database-native corridor query reduced 471 FIR NOTAMs to 79 — an 83%
off-route reduction before the bounded result reached the agents.

A prepared synthetic condition-card photograph contains a checked east-cove obstruction and a
hostile embedded instruction. Gemini extracts a typed, zero-authority proposal. Deterministic code
accepts the digest-bound obstruction, quarantines hostile text by hash, rejects east-cove plan v1,
and proposes west-cove plan v2 for pilot review. One authenticated pilot attestation may then create
one marked synthetic flight-following handoff. Replaying the same command returns the original
receipt without creating another handoff.

### How we built it

Seven named agents run on Google's Agent Development Kit with Gemini 3.5-or-newer Flash models:

`RouteAgent → IngestAgent → CorridorAgent → WeatherAgent → BriefingComposer → Verifier → DispatchAgent`

The private FastAPI agent runs on Cloud Run. Next.js exposes only a same-origin, exact-path relay
that authenticates to the agent with a keyless Cloud Run service identity and binds each command to
an opaque owner using HMAC. Cloud SQL for PostgreSQL/PostGIS owns the exact corridor query:
`ST_Intersects` filters GiST-indexed NOTAM areas against the buffered route while altitude/time
predicates remain in SQL rather than Python row paging. With `WATERLINE_SESSION_DB` configured, the
same service also persists ADK sessions, the constrained mission state graph, pilot attestations,
and the atomic at-most-once dispatch ledger. The browser cannot choose a recipient, actor, session
ID, evidence path, or agent endpoint.

### Challenges we ran into

NAV CANADA's endpoint accepts FIR/site queries but not geometry queries, so corridor and altitude
filtering had to be implemented in PostGIS. Gemini publisher availability also differed by endpoint;
we separated the global Vertex model location from the `us-central1` runtime/database region and
added a failure-tolerant model chain. The hardest product problem was making model output useful
while proving it had no authority: visual extraction, hostile text, provider callbacks, retries,
recovery, and concurrent replay all required separate deterministic gates.

### Accomplishments that we're proud of

- A live FIR feed collapses to the route corridor while the map assembles in real time.
- Valid visual evidence changes the plan; hostile visual text changes no trusted state.
- The full rejected-to-corrected mission survives refresh in Cloud SQL.
- One pilot attestation creates one atomic handoff claim; immediate replay returns the original receipt.
- A 126.98-second continuous deployed take proves the workflow with zero browser errors and no cuts.

### What we learned

Agent autonomy is more credible when its limits are visible. A wrong path, a quarantined
instruction, low-confidence inference, deterministic refusal, and replay suppression reveal more
engineering depth than an unexplained happy-path answer.

### What's next for Waterline

Configure the operator-owned SMS transport and capture one signed delivery callback using the same
at-most-once receipt contract. Then expand the curated destination resolver only with cited,
versioned water-aerodrome data while preserving the existing pilot and policy authority boundary.

## Built with

`Gemini 3.7 Flash` · `Gemini 3.6 Flash` · `Gemini 3.5 Flash` · `Google ADK` ·
`Google Gen AI SDK` · `Vertex AI` · `Cloud Run` · `Cloud SQL` · `Secret Manager` ·
`Artifact Registry` · `PostgreSQL/PostGIS` · `Next.js` · `FastAPI` · `MapLibre` ·
`Python` · `TypeScript`

Do not select or claim Agent Registry, Model Armor, Memory Bank, Agent Gateway, Gemma, Veo, Lyria,
Pub/Sub, Eventarc, Firestore, or GKE; those are not deployed Waterline dependencies.

## Try it out links

- Live app: https://waterline-web-2hjaxuzova-uc.a.run.app
- Code: https://github.com/Zen-cronic/waterline
- Video: `PENDING OPERATOR — upload the accepted film publicly to YouTube or Vimeo and verify logged-out 1080p playback.`

## Project Media — Image gallery

Upload in this order:

1. `../submission/waterline/gallery/architecture.png` — four authority lanes, trust plane, and exception arcs.
2. `../submission/waterline/gallery/01-live-evidence-awaiting-attestation.png` — live evidence, wrong path, and pilot boundary.
3. `../submission/waterline/gallery/02-replay-suppressed.png` — one original receipt and no duplicate handoff.

These are the visually inspected submission-package copies. Never replace them with contact PII or
secret-bearing screenshots.

## Video demo link

`PENDING OPERATOR — public YouTube or Vimeo URL for film/out/waterline-continuous-proof.mp4.`

Accepted local film: 1920×1080 H.264, 126.98 seconds, one continuous source, zero cuts/splices,
mission `mission-7b188e93d37e4a72bfe7`, receipt `c742fcdf…d11fbf`. The filmed consequence is the
no-network outbox; it does not claim SMS delivery. Operator upload bundle:
`../submission/waterline/video/`.

## Category

`PENDING OPERATOR — select exactly one category. Fortified Enterprise Fleet matches the bounded
multi-agent/security story, but Waterline does not deploy Agent Registry or Model Armor; confirm that
tradeoff before selecting it. Taskmaster is the safer fit for the complete evidence-to-handoff workflow.`

## Submitter type

`PENDING OPERATOR — choose exactly one: Individuals, Team of individuals, or Organization.`

## Submitter country of residence

`PENDING OPERATOR — select the operator's actual eligible country/territory. Do not infer this from
timezone, repository metadata, or account profile.`

## Organization name

`PENDING OPERATOR — this field is marked required by Devpost. If submitting as an Organization, use
its exact legal name. Otherwise follow the live form's individual/team instruction; do not invent an
organization.`

## What date did you start this project?

`08-20-26`

First commit: `9e27e62`, created during the August 3–31 submission period.

## Pre-existing code or work disclosure

The Waterline project was created during the submission period. It uses open-source frameworks and
libraries plus the public-domain OurAirports dataset. No sibling-project code or assets are included;
operational lessons from Cargo Release informed security and demo verification patterns.

## URL to your public or private code repo

https://github.com/Zen-cronic/waterline

Current audit state: **private**. Before submission, either make it public or grant both
`testing@devpost.com` and `cloudhackathons@google.com` access, as required by the official rules.

## Open-source license URL

https://github.com/Zen-cronic/waterline/blob/main/LICENSE

The root file is MIT and GitHub detects **MIT License** in the repository metadata.

## Did you add reproducible setup instructions to your README?

**Yes.** `README.md`, `TESTING.md`, and `DEPLOY.md` cover local setup, deterministic verification,
container contracts, private-agent/public-web deployment, and deployed proof.

## Hosted project URL

https://waterline-web-2hjaxuzova-uc.a.run.app

## Testing instructions (judge-only)

No login is required.

1. Open the live URL and keep the default CYYZ → Lady Evelyn Lake request.
2. Click **Brief this flight** and wait for **Human authority required**.
3. Inspect the typed condition-card extraction, hash-only quarantine, live NAV CANADA receipts,
   route-corridor map, bounded station inference, and east-v1-rejected → west-v2-pending decision.
4. Click **Attest & send one demo handoff** once. The active judge deployment uses the no-network
   outbox, so the UI should show one completed handoff receipt without contacting a phone.
5. Use the replay control. Confirm it returns the original receipt and displays replay suppression.

All people, phone contacts, route plans, and condition-card contents in this flow are synthetic.

## Which Google SDK did you use?

This is a **single-select** dropdown. Select **Agent Development Kit (ADK)** because it coordinates
the seven scoped agents. The description and Built with fields separately identify the Google Gen AI
SDK used for Vertex Gemini model turns and typed visual extraction.

## Which Google Cloud service(s) did you use?

Despite the plural label, Devpost exposes a **single-select** dropdown. Select **Cloud Run** because
it hosts the demonstrated public/private application. The description and Built with fields
separately identify Cloud SQL/PostGIS, Vertex AI, Secret Manager, and Artifact Registry. Do not
select undeployed managed-agent products.

## Architecture diagram

Upload `architecture/waterline-system.png` (1920×1080, 592,193 bytes). Editable source:
`architecture/waterline-system.svg`.

## Which Google AI models did you use?

- **Gemini 3.7 Flash:** accepted live visual-extraction and agent-path model in the deployed proof.
- **Gemini 3.6 Flash / Gemini 3.5 Flash:** failure-tolerant fallback chain; every option clears the
  event's Gemini 3.5-or-newer requirement.

Do not claim optional bonus models; none is integrated.

## Data sources

- Live NAV CANADA NOTAM and METAR feeds.
- Public-domain OurAirports station reference data.
- Waterline-created synthetic Lady Evelyn condition card, bound to a tracked digest and manifest.

`PENDING OPERATOR — third-party authorization for the undocumented NAV CANADA JSON endpoint is not
yet proven. Resolve or explicitly accept the risk recorded in
../submission/waterline/data-authorization-handoff.md before final submission; do not call it an
open or licensed API without evidence.`

## AI tools used during development

OpenAI Codex assisted with implementation, review, testing, deployment automation, and submission
drafting. Gemini accessed through Vertex AI is the load-bearing runtime model.

## Optional bonus — public build content

`PENDING OPERATOR — publish the corrected build story on a public platform and paste its URL.`

The draft is `/home/zin-kg/code/hackathons/allthingsagentic-2026/submission/waterline/blog.md`
and already includes the required disclosure that it was created for entering this hackathon.

## Optional bonus — social post

`PENDING OPERATOR — publish the corrected X or LinkedIn draft and paste its public URL.`

The draft is `/home/zin-kg/code/hackathons/allthingsagentic-2026/submission/waterline/social.md`
and includes `#AllThingsAgenticHackathon`.

## Startup Excellence opt-in

`PENDING OPERATOR — opt in only if submitting on behalf of an incorporated organization and able to
provide its exact name plus a corporate email address. Otherwise leave both optional startup fields
blank.`
