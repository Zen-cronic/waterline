import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

import { chromium } from "playwright";

const execFileAsync = promisify(execFile);
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const requiredApproval = "I_APPROVE_ONE_MARKED_SYNTHETIC_SMS_RECORDING";

if (process.env.WATERLINE_CONTINUOUS_RECORDING_APPROVED !== requiredApproval) {
  console.error("Continuous Waterline recording not started.");
  console.error("This take creates one live mission, records one pilot attestation, and may send at most one marked synthetic SMS to the server-allowlisted phone.");
  console.error(`Set WATERLINE_CONTINUOUS_RECORDING_APPROVED=${requiredApproval} to authorize it.`);
  process.exit(3);
}

const appUrl = (process.env.APP_URL ?? "https://waterline-web-2hjaxuzova-uc.a.run.app").replace(/\/$/, "");
const outputDirectory = path.resolve(process.env.RECORDING_OUTPUT ?? "../.playwright-mcp/continuous-demo");
const rawVideoPath = path.join(outputDirectory, "waterline-continuous-proof.webm");
const finalVideoPath = path.join(outputDirectory, "waterline-continuous-proof.mp4");
const reportPath = path.join(outputDirectory, "waterline-continuous-proof.json");
const viewport = { width: 1920, height: 1080 };

await mkdir(outputDirectory, { recursive: true });
const startedAt = Date.now();
const beats = [];
const browserErrors = [];
let missionId = null;
let receiptId = null;
let recordingError = null;
let providerDelivered = false;
let replaySuppressed = false;

function mark(name, detail = undefined) {
  const at = Number(((Date.now() - startedAt) / 1000).toFixed(2));
  beats.push({ name, at, ...(detail ? { detail } : {}) });
  console.log(`[${at.toFixed(1).padStart(6)}s] ${name}`);
}

async function showCaption(page, caption) {
  await page.evaluate((text) => {
    document.querySelector("#waterline-proof-caption")?.remove();
    const element = document.createElement("div");
    element.id = "waterline-proof-caption";
    element.textContent = text;
    element.style.cssText = [
      "position:fixed", "left:50%", "bottom:30px", "transform:translateX(-50%)",
      "z-index:2147483647", "max-width:1280px", "padding:13px 22px",
      "border:1px solid rgba(53,208,214,.72)", "border-radius:10px",
      "background:rgba(4,12,20,.95)", "color:#f4f8ff",
      "font:600 22px/1.35 Arial,sans-serif", "text-align:center", "pointer-events:none",
    ].join(";");
    document.body.append(element);
  }, caption);
}

async function hold(page, name, caption, milliseconds) {
  await showCaption(page, caption);
  mark(name);
  await sleep(milliseconds);
  await page.evaluate(() => document.querySelector("#waterline-proof-caption")?.remove());
  await sleep(500);
}

async function durationSeconds(filePath) {
  const { stdout } = await execFileAsync("ffprobe", [
    "-v", "error", "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1", filePath,
  ]);
  return Number(stdout.trim());
}

const health = await fetch(`${appUrl}/api/waterline/missions/not-a-mission`, { redirect: "manual" });
assert.ok([400, 404].includes(health.status), "Exact-path public relay must reject an invalid mission route");

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport,
  deviceScaleFactor: 1,
  colorScheme: "dark",
  recordVideo: { dir: outputDirectory, size: viewport },
});
const page = await context.newPage();
const video = page.video();
page.on("pageerror", (error) => browserErrors.push(error.message));
page.on("console", (message) => {
  if (message.type() === "error" && !message.text().includes("favicon") &&
      !message.text().startsWith("Failed to load resource:")) {
    browserErrors.push(message.text());
  }
});
page.on("response", (response) => {
  if (response.status() < 400) return;
  browserErrors.push(`${response.status()} ${response.url()}`);
});

try {
  await page.goto(appUrl, { waitUntil: "domcontentloaded", timeout: 45_000 });
  await page.getByRole("heading", { name: "Waterline" }).waitFor();
  await hold(
    page,
    "B0 station-less route",
    "This lake has no identifier and no weather station. Waterline can propose a path; agents cannot authorize the consequence.",
    7_000,
  );

  await page.getByRole("button", { name: "Brief this flight" }).click();
  await page.getByText("Human authority required", { exact: true }).waitFor({ timeout: 150_000 });
  missionId = await page.locator(".mission-chip span").first().textContent();
  assert.match(missionId ?? "", /^mission-[0-9a-f]{20}$/);
  await page.screenshot({ path: path.join(outputDirectory, "01-awaiting-attestation.png") });
  await hold(
    page,
    "B1 live evidence and wrong path",
    "Live NAV CANADA evidence collapses to the corridor. The photographed card rejects east cove, while hostile text is quarantined by hash.",
    16_000,
  );

  const proofRail = page.locator(".proof-rail");
  await proofRail.evaluate((element) => { element.scrollTop = element.scrollHeight * 0.42; });
  await page.getByText("Inference, not measurement", { exact: false }).waitFor();
  await hold(
    page,
    "B2 bounded inference and provenance",
    "The nearest real station, confidence, raw METAR, source receipts, deterministic gates, and model authority zero stay visible.",
    14_000,
  );

  await proofRail.evaluate((element) => { element.scrollTop = 0; });
  await page.getByRole("button", { name: "Attest & send one demo handoff" }).click();
  await page.getByText(/PROVIDER ACCEPTED|DELIVERED|HANDOFF COMPLETED/).waitFor({ timeout: 90_000 });
  receiptId = (await page.getByText(/^receipt /).textContent())?.replace(/^receipt /, "") ?? null;
  assert.ok(receiptId);
  await page.getByText(/PROVIDER ACCEPTED|DELIVERED|HANDOFF COMPLETED/).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(outputDirectory, "02-provider-accepted.png") });
  await hold(
    page,
    "B3 one human action to one external consequence",
    "One authenticated pilot attestation created one marked synthetic handoff to a server-allowlisted responsible person.",
    14_000,
  );

  await page.getByText("DELIVERED", { exact: true }).waitFor({ timeout: 90_000 });
  await page.getByText("DELIVERED", { exact: true }).scrollIntoViewIfNeeded();
  providerDelivered = true;
  await page.screenshot({ path: path.join(outputDirectory, "03-delivered.png") });
  await hold(
    page,
    "B4 independently verified delivery",
    "The create call was only provider-accepted. This DELIVERED state came from a separately signed provider callback.",
    13_000,
  );

  await page.getByRole("button", { name: "Replay same command · prove no second SMS" }).click();
  await page.getByText("REPLAY SUPPRESSED · ORIGINAL RECEIPT RETURNED", { exact: true }).waitFor();
  assert.equal((await page.getByText(/^receipt /).textContent())?.replace(/^receipt /, ""), receiptId);
  replaySuppressed = true;
  await page.getByText("REPLAY SUPPRESSED · ORIGINAL RECEIPT RETURNED", { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(outputDirectory, "04-replay-suppressed.png") });
  await hold(
    page,
    "B5 original receipt replay",
    "The identical command returns the original receipt. There is still one provider reference and no second SMS.",
    14_000,
  );

  await proofRail.evaluate((element) => { element.scrollTop = 0; });
  await hold(
    page,
    "B6 close on bounded authority",
    "Agents assembled the briefing. The pilot and deterministic policy kept the key.",
    8_000,
  );
  mark("END — browser assertions passed", { mission_id: missionId, receipt_id: receiptId });
} catch (error) {
  recordingError = error;
  mark("FAILED", { error: error instanceof Error ? error.message : String(error) });
} finally {
  await page.close();
  await context.close();
  await browser.close();
}

if (!video) throw new Error("Playwright did not create a video artifact");
await rename(await video.path(), rawVideoPath);
await execFileAsync("ffmpeg", [
  "-hide_banner", "-loglevel", "error", "-y", "-i", rawVideoPath,
  "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
  "-pix_fmt", "yuv420p", "-movflags", "+faststart", finalVideoPath,
]);

const rawDuration = await durationSeconds(rawVideoPath);
const finalDuration = await durationSeconds(finalVideoPath);
const sha256 = createHash("sha256").update(await readFile(finalVideoPath)).digest("hex");
const verificationErrors = [];
if (recordingError) verificationErrors.push(recordingError instanceof Error
  ? recordingError.stack ?? recordingError.message : String(recordingError));
if (finalDuration >= 240) verificationErrors.push(`Take is ${finalDuration}s; cap is under 240s`);
if (Math.abs(rawDuration - finalDuration) > 0.25) verificationErrors.push("Transcode changed duration");
if (browserErrors.length) verificationErrors.push(`Browser errors: ${browserErrors.join(" | ")}`);
if (!providerDelivered) verificationErrors.push("Signed provider callback did not reach DELIVERED");
if (!replaySuppressed) verificationErrors.push("Original-receipt replay suppression was not proved");
const report = {
  status: verificationErrors.length ? "FAILED" : "PASSED",
  app_url: appUrl,
  transport: "sms",
  started_at: new Date(startedAt).toISOString(),
  finished_at: new Date().toISOString(),
  continuous: true,
  cuts_or_splices: 0,
  viewport,
  mission_id: missionId,
  receipt_id: receiptId,
  final_state: {
    boundary: "human",
    outbound_mode: "sms",
    delivery: providerDelivered ? "delivered" : null,
    duplicate_suppressed: replaySuppressed,
  },
  beats,
  browser_errors: browserErrors,
  artifacts: { rawVideoPath, finalVideoPath, rawDuration, finalDuration, sha256 },
  verification_errors: verificationErrors,
};
await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
if (verificationErrors.length) {
  throw new Error(`Continuous proof failed: ${verificationErrors.join(" | ")}`);
}
console.log(`continuous proof passed: ${finalDuration.toFixed(2)}s, sha256:${sha256}`);
