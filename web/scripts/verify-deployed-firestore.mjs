import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

import { chromium } from "playwright";

const execFileAsync = promisify(execFile);
const requiredApproval = "I_APPROVE_FIRESTORE_FLIGHT_FOLLOWING_PROOF";
if (process.env.WATERLINE_FIRESTORE_PROOF_APPROVED !== requiredApproval) {
  console.error("Deployed Firestore proof not started.");
  console.error("This creates one live mission, one pilot attestation, and ephemeral room messages.");
  console.error(`Set WATERLINE_FIRESTORE_PROOF_APPROVED=${requiredApproval} to authorize it.`);
  process.exit(3);
}

const appUrl = (process.env.APP_URL ?? "https://waterline-web-2hjaxuzova-uc.a.run.app").replace(/\/$/, "");
const project = process.env.WATERLINE_PROJECT ?? "ata-2026-waterline";
const region = process.env.WATERLINE_REGION ?? "us-central1";
const outputDirectory = path.resolve(process.env.FIRESTORE_PROOF_OUTPUT ?? "../.playwright-mcp/deployed-firestore");
const reportPath = path.join(outputDirectory, "deployed-firestore-proof.json");
const browserErrors = [];

function collectErrors(page, surface) {
  page.on("pageerror", (error) => browserErrors.push(`${surface}: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().includes("favicon")) {
      browserErrors.push(`${surface}: ${message.text()}`);
    }
  });
}

function capabilitySummary(inviteUrl) {
  const token = decodeURIComponent(new URL(inviteUrl).pathname.split("/").at(-1));
  const encoded = token.slice(0, token.lastIndexOf("."));
  return { token, payload: JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")) };
}

async function firestoreJson(accessToken, suffix) {
  const response = await fetch(
    `https://firestore.googleapis.com/v1/projects/${project}/databases/(default)/documents/${suffix}`,
    { headers: { authorization: `Bearer ${accessToken}`, "x-goog-user-project": project } },
  );
  const body = await response.json().catch(() => ({}));
  assert.equal(response.ok, true, `Firestore inspection failed: ${response.status} ${JSON.stringify(body)}`);
  return body;
}

async function waitUnderTwoSeconds(action, assertion, label) {
  const started = performance.now();
  await action();
  await assertion();
  const elapsedMs = Math.round(performance.now() - started);
  assert.ok(elapsedMs <= 2_000, `${label} took ${elapsedMs}ms; expected no more than 2000ms`);
  return elapsedMs;
}

await mkdir(outputDirectory, { recursive: true });
const browser = await chromium.launch({ headless: true });
const pilotContext = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  colorScheme: "dark",
  permissions: ["clipboard-read", "clipboard-write"],
});
const followerContext = await browser.newContext({
  viewport: { width: 390, height: 844 },
  colorScheme: "dark",
});
const pilot = await pilotContext.newPage();
const follower = await followerContext.newPage();
collectErrors(pilot, "pilot");
collectErrors(follower, "follower");

let proofError = null;
let missionId = null;
let receiptId = null;
let invitation = null;
let acknowledgementMs = null;
let followerToPilotMs = null;
let pilotToFollowerMs = null;
let firestoreEvidence = null;

try {
  await pilot.goto(appUrl, { waitUntil: "networkidle", timeout: 60_000 });
  await pilot.getByRole("heading", { name: "Waterline" }).waitFor();
  await pilot.getByRole("button", { name: "Brief this flight" }).click();
  await pilot.locator(".mission-chip span").first().waitFor({ timeout: 60_000 });
  missionId = await pilot.locator(".mission-chip span").first().textContent();
  assert.match(missionId ?? "", /^mission-[0-9a-f]{20}$/);

  const boundary = await Promise.race([
    pilot.getByText("Human authority required", { exact: true })
      .waitFor({ timeout: 240_000 }).then(() => "human"),
    pilot.getByText("Review required — handoff held", { exact: true })
      .waitFor({ timeout: 240_000 }).then(() => "held"),
    pilot.locator(".verdict.no").first().waitFor({ timeout: 240_000 }).then(() => "failed"),
  ]);
  assert.equal(boundary, "human", `Mission reached ${boundary} instead of the pilot handoff gate`);

  await pilot.getByRole("button", { name: "Attest & open follower room" }).click();
  await pilot.getByText("HANDOFF READY · WAITING FOR FOLLOWER", { exact: true })
    .waitFor({ timeout: 90_000 });
  await pilot.getByText("LIVE VIA FIRESTORE", { exact: true }).first().waitFor({ timeout: 30_000 });
  receiptId = (await pilot.getByText(/^receipt /).textContent())?.replace(/^receipt /, "") ?? null;
  assert.match(receiptId ?? "", /^[0-9a-f]{64}$/);

  await pilot.getByRole("button", { name: "Copy follower link" }).click();
  invitation = await pilot.evaluate(() => navigator.clipboard.readText());
  assert.ok(invitation.startsWith(`${appUrl}/handoff/`), "Copied invitation does not target the deployed app");
  const firstCapability = capabilitySummary(invitation);
  assert.equal(firstCapability.payload.mission_id, missionId);
  assert.ok(firstCapability.payload.expires_at > Math.floor(Date.now() / 1000));

  await follower.goto(invitation, { waitUntil: "networkidle", timeout: 60_000 });
  await follower.getByText("LIVE VIA FIRESTORE", { exact: true }).waitFor({ timeout: 30_000 });
  acknowledgementMs = await waitUnderTwoSeconds(
    () => follower.getByRole("button", { name: "Acknowledge flight following" }).click(),
    () => pilot.getByText("FOLLOWING ACTIVE · LIVE VIA FIRESTORE", { exact: true }).waitFor(),
    "Follower acknowledgement propagation",
  );

  const followerMessage = "Route received. I’ll monitor the ETA.";
  followerToPilotMs = await waitUnderTwoSeconds(
    async () => {
      await follower.getByRole("textbox", { name: "Coordination message" }).fill(followerMessage);
      await follower.getByRole("button", { name: "Send" }).click();
    },
    () => pilot.getByText(followerMessage, { exact: true }).waitFor(),
    "Follower-to-pilot message propagation",
  );

  const pilotMessage = "Copy. West cove remains the candidate sector.";
  pilotToFollowerMs = await waitUnderTwoSeconds(
    async () => {
      await pilot.getByRole("textbox", { name: "Coordination message" }).fill(pilotMessage);
      await pilot.getByRole("button", { name: "Send" }).click();
    },
    () => follower.getByText(pilotMessage, { exact: true }).waitFor(),
    "Pilot-to-follower message propagation",
  );

  await follower.reload({ waitUntil: "networkidle", timeout: 60_000 });
  await follower.getByText("LIVE VIA FIRESTORE", { exact: true }).waitFor({ timeout: 30_000 });
  await follower.getByText(followerMessage, { exact: true }).waitFor();
  await follower.getByText(pilotMessage, { exact: true }).waitFor();

  await pilot.getByRole("button", { name: "Replay handoff · prove one room" }).click();
  await pilot.getByText("REPLAY SUPPRESSED · ORIGINAL RECEIPT RETURNED", { exact: true })
    .waitFor({ timeout: 60_000 });
  assert.equal((await pilot.getByText(/^receipt /).textContent())?.replace(/^receipt /, ""), receiptId);
  await pilot.getByRole("button", { name: "Copy follower link" }).click();
  const replayInvitation = await pilot.evaluate(() => navigator.clipboard.readText());
  const replayCapability = capabilitySummary(replayInvitation);
  assert.equal(replayInvitation, invitation, "Replay did not reproduce the identical invitation");
  assert.equal(replayCapability.payload.expires_at, firstCapability.payload.expires_at);

  const { stdout: accessToken } = await execFileAsync("gcloud", ["auth", "print-access-token"]);
  const thread = await firestoreJson(accessToken.trim(), `handoff_threads/${missionId}`);
  const members = await firestoreJson(accessToken.trim(), `handoff_threads/${missionId}/members?pageSize=10`);
  const messages = await firestoreJson(accessToken.trim(), `handoff_threads/${missionId}/messages?pageSize=50`);
  const roles = (members.documents ?? []).map((document) => document.fields?.role?.stringValue).sort();
  const messageRows = messages.documents ?? [];
  assert.deepEqual(roles, ["follower", "pilot"]);
  assert.equal(thread.fields?.missionId?.stringValue, missionId);
  assert.equal(messageRows.filter((document) => document.fields?.kind?.stringValue === "ack").length, 1);
  assert.ok(messageRows.some((document) => document.fields?.body?.stringValue === followerMessage));
  assert.ok(messageRows.some((document) => document.fields?.body?.stringValue === pilotMessage));
  firestoreEvidence = {
    thread_name: thread.name,
    member_count: roles.length,
    roles,
    message_count: messageRows.length,
    acknowledgement_count: 1,
  };

  await pilot.screenshot({ path: path.join(outputDirectory, "pilot-following-active.png"), fullPage: true });
  await follower.screenshot({ path: path.join(outputDirectory, "phone-reconnected.png"), fullPage: true });
} catch (error) {
  proofError = error;
  await pilot.screenshot({ path: path.join(outputDirectory, "pilot-failure.png"), fullPage: true }).catch(() => undefined);
  await follower.screenshot({ path: path.join(outputDirectory, "follower-failure.png"), fullPage: true }).catch(() => undefined);
} finally {
  await pilotContext.close();
  await followerContext.close();
  await browser.close();
}

const report = {
  status: proofError || browserErrors.length ? "FAILED" : "PASSED",
  app_url: appUrl,
  project,
  region,
  mission_id: missionId,
  receipt_id: receiptId,
  invitation_sha256_not_recorded: true,
  acknowledgement_ms: acknowledgementMs,
  follower_to_pilot_ms: followerToPilotMs,
  pilot_to_follower_ms: pilotToFollowerMs,
  replay_identical: proofError ? false : true,
  firestore: firestoreEvidence,
  browser_errors: browserErrors,
  error: proofError instanceof Error ? proofError.message : proofError ? String(proofError) : null,
  verified_at: new Date().toISOString(),
};
await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
console.log(JSON.stringify(report, null, 2));
if (proofError) throw proofError;
assert.deepEqual(browserErrors, [], `Browser errors: ${browserErrors.join(" | ")}`);
