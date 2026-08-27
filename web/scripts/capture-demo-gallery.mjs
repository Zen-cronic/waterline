import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { chromium } from "playwright";

const appUrl = (process.env.APP_URL ?? "http://127.0.0.1:3010").replace(/\/$/, "");
const outputDirectory = path.resolve(process.env.GALLERY_OUTPUT ?? "../.playwright-mcp/sms-gallery");
const missionId = "mission-0123456789abcdefabcd";
const traceId = "trace-gallery-sms-proof";
const providerReference = `SM${"a".repeat(32)}`;
const receiptId = "receipt-gallery-at-most-once";
let stage = "awaiting";
let attestationCommands = 0;

await mkdir(outputDirectory, { recursive: true });

const modelReceipt = {
  receipt_id: "model-receipt-gallery", source_ref: "prepared://lady-evelyn/condition-card-v1",
  artifact_sha256: "a".repeat(64), schema_version: "waterline.condition-card.extraction.v1",
  extractor: "gemini-3.7-flash", validation_result: "accepted", confidence: 0.98,
  reason_codes: [], trace_id: traceId, dispatch_authority: false,
};
const plan = {
  rejected_plan: { plan_id: "plan-v1", landing_sector: "east", status: "rejected", reason_code: "east_cove_obstructed" },
  corrected_plan: { plan_id: "plan-v2", landing_sector: "west", status: "proposed_pending_pilot" },
  dispatch_authority: false, trace_id: traceId,
};
const inference = {
  available: true, confidence: 0.16, reach_nm: 27.8,
  confidence_note: "nearest station 27.8 NM away; 3 stations within range",
  inferred: { wind_dir: 240, wind_kt: 12, vis_sm: 10, ceiling_ft: 4200 },
  sources: [{ station_id: "CYXR", dist_nm: 27.8, metar_raw: "CYXR 261200Z 24012G18KT 10SM BKN042" }],
};
const provenance = {
  site: "CZYZ",
  notam: { source_mode: "live", source_ref: "navcanada:notam", records: 456, parsed: 456, payload_sha256: "c".repeat(64) },
  metar: { source_mode: "live", source_ref: "navcanada:metar", records: 53, parsed: 29, payload_sha256: "d".repeat(64) },
};
const proof = {
  briefing: "HAZARDS\nEAST cove is obstructed; plan v2 proposes WEST cove pending pilot review.\nWEATHER\nINFERRED from CYXR, 27.8 NM away. PILOT REVIEW REQUIRED.",
  semantic_verdict: "APPROVED — inferred weather and source evidence agree.",
  inference, provenance,
  briefing_gate: { approved: true, reasons: [] },
  dispatch_gate: { approved: false, reasons: ["authenticated pilot attestation is missing"] },
};

function event(id, from, to, eventType, reasonCode, evidence = {}) {
  return { event_id: id, from_status: from, to_status: to, event_type: eventType, reason_code: reasonCode, evidence, trace_id: traceId };
}

function timeline() {
  const events = [
    event("event-1", null, "proposed", "mission_proposed", "authenticated_intake"),
    event("event-2", "proposed", "proposed", "condition_card_evaluated", "condition_card_validated", {
      model_receipt: modelReceipt,
      trusted_evidence: {
        card_id: "WL-LEL-20260826-A", lake_name: "Lady Evelyn Lake", blocked_sector: "east",
        obstruction: "LOG BOOM ACROSS APPROACH", valid_from: "2026-08-26T12:00:00Z",
        valid_until: "2026-09-07T23:59:00Z", source_ref: modelReceipt.source_ref,
        artifact_sha256: modelReceipt.artifact_sha256, confidence: 0.98, dispatch_authority: false,
      }, plan_revision: plan,
    }),
    event("event-3", "proposed", "proposed", "condition_card_quarantined", "embedded_instruction_excluded", {
      quarantine_receipt: {
        receipt_id: "quarantine-gallery", category: "embedded_instruction", content_sha256: "b".repeat(64),
        action: "excluded_from_trusted_state", dispatch_authority: false, trace_id: traceId,
      },
    }),
    event("event-4", "proposed", "proposed", "briefing_evidence_recorded", "deterministic_briefing_gate_passed", proof),
    event("event-5", "proposed", "rejected", "plan_revision_required", "east_cove_obstructed", { plan_revision: plan }),
    event("event-6", "rejected", "awaiting_attestation", "pilot_review_requested", "owner_attestation_required"),
  ];
  if (stage !== "awaiting") {
    events.push(
      event("event-7", "awaiting_attestation", "corrected", "pilot_attestation_recorded", "owner_attested"),
      event("event-8", "corrected", "accepted", "proposal_accepted", "deterministic_gate_passed"),
      event("event-9", "accepted", "dispatched", "dispatch_completed", "verified_notice_receipt", {
        receipt_id: receiptId, attestation_id: "attestation-gallery", channel: "sms",
        provider_reference: providerReference, provider_status: "queued",
        recipient_redacted: "+1••••••1234", status: "provider_accepted", at_most_once: true,
      }),
    );
  }
  if (stage === "delivered" || stage === "replay") {
    events.push(event("event-10", "dispatched", "dispatched", "delivery_status_updated", "provider_delivered", {
      receipt_id: receiptId, provider_reference: providerReference, provider_status: "delivered",
      recipient_redacted: "+1••••••1234",
    }));
  }
  if (stage === "replay") {
    events.push(event("event-11", "dispatched", "dispatched", "dispatch_replayed", "duplicate_suppressed", {
      receipt_id: receiptId, provider_reference: providerReference, provider_status: "delivered",
      duplicate_suppressed: true, at_most_once: true,
    }));
  }
  return events;
}

function mission(status = stage === "awaiting" ? "awaiting_attestation" : "dispatched") {
  return { mission_id: missionId, owner_ref: "pilot-gallery-owner", trace_id: traceId, status };
}

function sse(value) {
  return `data: ${JSON.stringify(value)}\n\n`;
}

const stream = [
  { type: "mission", ...mission("proposed"), event: timeline()[0] },
  { type: "layer", layer: "route", label: "CYYZ → Lady Evelyn Lake", rev: "1", status: "ready", rowCount: 1, geojson: { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [[-79.63, 43.68], [-80.14, 47.32]] } }] } },
  { type: "layer", layer: "corridor", label: "route corridor ±10 NM", rev: "1", status: "ready", rowCount: 1, geojson: { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[[-79.82, 43.68], [-79.43, 43.68], [-79.95, 47.34], [-80.33, 47.30], [-79.82, 43.68]]] } }] } },
  { type: "layer", layer: "stations", label: "3 source stations", rev: "1", status: "ready", rowCount: 1, geojson: { type: "FeatureCollection", features: [{ type: "Feature", properties: { station_id: "CYXR" }, geometry: { type: "Point", coordinates: [-79.85, 47.70] } }] } },
  { type: "step", agent: "IngestAgent", kind: "live", detail: "NAV CANADA NOTAM 456/456 · METAR 29/53" },
  { type: "step", agent: "CorridorAgent", kind: "filtered", detail: "PostGIS retained 86 on-route notices" },
  { type: "panel", key: "condition_card", value: { image_url: "/evidence/lady-evelyn-condition-card-v1.png", validation_result: "accepted", reason_codes: [], model_receipt: modelReceipt, trusted_evidence: timeline()[1].evidence.trusted_evidence } },
  { type: "panel", key: "quarantine", value: timeline()[2].evidence.quarantine_receipt },
  { type: "panel", key: "plan_revision", value: plan },
  { type: "panel", key: "inference", value: inference },
  { type: "panel", key: "provenance", value: provenance },
  { type: "panel", key: "mission_proof", value: proof },
  { type: "agent", author: "BriefingComposer", final: true, text: proof.briefing },
  { type: "agent", author: "Verifier", final: true, text: proof.semantic_verdict },
  { type: "mission", ...mission("rejected"), event: timeline()[4] },
  { type: "mission", ...mission("awaiting_attestation"), event: timeline()[5] },
  { type: "done" },
].map(sse).join("");

async function installRoutes(page) {
  await page.route("**/api/waterline/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname === "/api/waterline/missions") {
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: stream });
      return;
    }
    if (request.method() === "GET" && url.pathname === `/api/waterline/missions/${missionId}`) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ mission: mission(), events: timeline() }) });
      return;
    }
    if (request.method() === "POST" && url.pathname === `/api/waterline/missions/${missionId}/attest`) {
      attestationCommands += 1;
      const duplicate = stage === "delivered" || stage === "replay";
      stage = duplicate ? "replay" : "provider_accepted";
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        ...mission("dispatched"), receipt_id: receiptId, attestation_id: "attestation-gallery",
        events: timeline().slice(6), authority: { approved: true, reasons: [] },
        dispatch: {
          receipt_id: receiptId, attestation_id: "attestation-gallery", mission_id: missionId,
          trace_id: traceId, sent: true, channel: "sms", provider_reference: providerReference,
          provider_status: duplicate ? "delivered" : "queued", recipient_redacted: "+1••••••1234",
          duplicate_suppressed: duplicate, status: duplicate ? "delivered" : "provider_accepted",
          at_most_once: true,
        },
      }) });
      return;
    }
    await route.continue();
  });
}

async function captureProofState(page, label, outputName) {
  await page.getByText(label, { exact: true }).scrollIntoViewIfNeeded();
  await page.waitForTimeout(150);
  await page.screenshot({ path: path.join(outputDirectory, outputName) });
}

function attachDiagnostics(page, errors) {
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
      errors.push(message.text());
    }
  });
  page.on("response", (response) => {
    if (response.status() < 400) return;
    errors.push(`${response.status()} ${response.url()}`);
  });
}

const browser = await chromium.launch({ headless: true });
const errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: "dark" });
  attachDiagnostics(page, errors);
  await installRoutes(page);
  await page.goto(appUrl, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "Brief this flight" }).click();
  await page.getByText("Human authority required", { exact: true }).waitFor();
  await page.locator("#map canvas").waitFor();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(outputDirectory, "01-wrong-path-awaiting.png") });

  await page.getByRole("button", { name: "Attest & send one demo handoff" }).click();
  await page.getByText("PROVIDER ACCEPTED", { exact: true }).waitFor();
  await captureProofState(page, "PROVIDER ACCEPTED", "02-provider-accepted.png");

  stage = "delivered";
  await page.getByText("DELIVERED", { exact: true }).waitFor();
  await captureProofState(page, "DELIVERED", "03-delivered.png");

  await page.getByRole("button", { name: "Replay same command · prove no second SMS" }).click();
  await page.getByText("REPLAY SUPPRESSED · ORIGINAL RECEIPT RETURNED", { exact: true }).waitFor();
  await captureProofState(page, "REPLAY SUPPRESSED · ORIGINAL RECEIPT RETURNED", "04-replay.png");
  assert.equal(attestationCommands, 2);
  assert.equal(await page.locator("body").evaluate((body) => body.scrollWidth <= window.innerWidth), true);

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: "dark" });
  attachDiagnostics(mobile, errors);
  await installRoutes(mobile);
  await mobile.goto(appUrl, { waitUntil: "domcontentloaded" });
  await mobile.getByRole("button", { name: "Brief this flight" }).click();
  await mobile.getByText("Human authority required", { exact: true }).waitFor();
  await mobile.getByRole("button", { name: "Attest & send one demo handoff" }).click();
  await mobile.getByText("REPLAY SUPPRESSED · ORIGINAL RECEIPT RETURNED", { exact: true }).waitFor();
  await mobile.locator("#map canvas").waitFor();
  await mobile.waitForTimeout(2000);
  assert.equal(await mobile.locator("body").evaluate((body) => body.scrollWidth <= window.innerWidth), true);
  await mobile.screenshot({ path: path.join(outputDirectory, "05-mobile-delivered-replay.png"), fullPage: true });
  await mobile.close();
  await page.close();
} finally {
  await browser.close();
}

const report = { status: errors.length ? "FAILED" : "PASSED", app_url: appUrl, stage, errors };
await writeFile(path.join(outputDirectory, "gallery-report.json"), `${JSON.stringify(report, null, 2)}\n`);
assert.deepEqual(errors, []);
console.log(`gallery proof passed: ${outputDirectory}`);
