# Waterline architecture

![Waterline bounded-authority architecture](architecture/waterline-system.svg)

The diagram is a 1920×1080, submission-ready view of the architecture that actually exists before preview deployment. It makes five roles visible without relying on model prose: the reader, writer, authorizer, security boundary, and physical/digital consequence.

## Four lanes

| Lane | Responsibility | Authority |
|---|---|---|
| Authenticated intake | Accept only route fields, issue an opaque owner session, attach the server-selected evidence artifact, and sign the exact command | `REQUEST` |
| Gemini + ADK fleet | Extract typed visual fields, fetch/rank source data, filter geometry, infer, compose, and propose | `READ / PROPOSE`; dispatch authority is always false |
| Deterministic authority | Validate safe fields, quarantine hostile content, write the constrained state graph, verify the pilot attestation, and atomically claim dispatch | `VALIDATE / RECORD`; the authenticated pilot is the sole authorizer |
| Observable consequence | Stream the map and proof rail, show east rejection → west acceptance, retain recovery evidence, and display dispatch/replay receipts | Receives verified facts and receipts; cannot grant authority |

The public/private security boundary is between the same-origin Next.js relay and the private FastAPI agent. Cloud Run IAM authenticates the web service to the agent. HMAC binds the opaque owner, timestamp, HTTP method, normalized path, and normalized body. Browser input cannot select an actor, user/session ID, evidence path, recipient during intake, or dispatch instruction.

## Trust plane: implemented versus deferred

| Capability | Iteration 6 status | Truthful proof |
|---|---|---|
| Identity | Implemented locally; cloud identities provisioned | Separate keyless `waterline-web` and `waterline-runtime` service accounts; signed HttpOnly pilot session; owner-bound API tests |
| Gateway | Implemented as the application boundary | Exact-path same-origin relay, Cloud Run IAM ID token, and HMAC request binding. This is not a claim that Google Cloud Agent Gateway is deployed. |
| Model Armor | Deferred | Not enabled and not claimed. Hash-only hostile-text quarantine and deterministic validation are application controls, not Model Armor. |
| Registry | Deferred | No Agent Registry resource exists and none is claimed. The seven-agent roster is code-defined. |
| Memory | Implemented | ADK `DatabaseSessionService`, missions, append-only events, attestations, and dispatch receipts in Cloud SQL/PostgreSQL |
| Observability | Implemented in-app; managed proof pending deployment | Trace IDs, event IDs, reason codes, source/model/gate receipts, and durable recovery are visible now. Cloud Run revision/request logs become deployment evidence in iteration 7. |

This status split is deliberate. Model Armor and Registry appear in the trust plane so a judge can see where they would sit, while dashed styling and explicit “deferred” labels prevent an architecture aspiration from becoming a false deployed-service claim.

## Exception paths

1. **Hostile or ambiguous evidence → quarantine/review.** The visual model proposes fields with zero authority. Deterministic checks bind the artifact digest, schema/template, lake/route, sector, validity, required fields, and confidence. Hostile text survives only as a content hash and category. Any ambiguity requests pilot review and skips the agent pipeline.
2. **Condition-card rejection → corrected route revision.** The accepted east-cove obstruction rejects plan v1. The fleet may propose west-cove v2, but only the deterministic gate and authenticated pilot attestation can accept it. The retained reason and receipts make the red-to-green consequence inspectable after refresh.

Only three semantic edge types appear in the diagram: read-only inspection, bounded human/resume action, and verified receipt. The separation prevents a visually convenient arrow from implying authority the source component does not have.

## Rubric traceability

| Rubric axis | Architectural proof | Judge-visible proof |
|---|---|---|
| Innovation | Geometry-keyed briefing for one of five curated station-less water destinations; typed multimodal condition evidence changes the plan | Live route/corridor map; visual card; inference confidence and raw METAR provenance; east rejection → west correction |
| Architecture | Private agent, exact-path signed relay, server-owned identity, read/propose-only agents, deterministic writer/gates, sole pilot authorization, durable ledger, at-most-once claim | Authority Map; trace/event/reason IDs; quarantine; gate cards; verified dispatch and replay receipts |
| Demo | One continuous state machine with a retained failure/recovery beat and a single consequential action | Awaiting, degraded, recovered, dispatched, and replay-suppressed cockpit states; no hidden model authority |
| Google Cloud | Cloud Run source contracts, Vertex Gemini adapter, Cloud SQL/PostGIS, Secret Manager, Artifact Registry, keyless IAM | The deployed URL, private agent revision/request logs, Cloud SQL instance/data, Vertex activity, and image/build digests are iteration 7 proof—not yet claimed |

## Artifact use

- Source of truth: [`architecture/waterline-system.svg`](architecture/waterline-system.svg)
- Raster export for slides and submission media: `architecture/waterline-system.png`
- Both exports are 1920×1080. Regenerate the PNG from the tracked SVG after any diagram change and inspect it at full size and at a 1080p fit before use.

The diagram contains no secret values, service URLs, account identifiers, contact PII, raw model reasoning, or operational flight guidance.
