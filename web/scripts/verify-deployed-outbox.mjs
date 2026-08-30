import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

import { chromium } from "playwright";

const execFileAsync = promisify(execFile);
const requiredApproval = "I_APPROVE_OUTBOX_ONLY_ATTESTATION";

if (process.env.WATERLINE_OUTBOX_PROOF_APPROVED !== requiredApproval) {
  console.error("Deployed outbox proof not started.");
  console.error("This creates one live mission and pilot attestation, but must never use an external transport.");
  console.error(`Set WATERLINE_OUTBOX_PROOF_APPROVED=${requiredApproval} to authorize it.`);
  process.exit(3);
}

const appUrl = (process.env.APP_URL ?? "https://waterline-web-2hjaxuzova-uc.a.run.app").replace(/\/$/, "");
const project = process.env.WATERLINE_PROJECT ?? "ata-2026-waterline";
const region = process.env.WATERLINE_REGION ?? "us-central1";
const agentService = process.env.WATERLINE_AGENT_SERVICE ?? "waterline-agent";
const outputDirectory = path.resolve(process.env.OUTBOX_PROOF_OUTPUT ?? "../.playwright-mcp/deployed-outbox");
const reportPath = path.join(outputDirectory, "deployed-outbox-proof.json");
const awaitingScreenshotPath = path.join(outputDirectory, "deployed-outbox-awaiting.png");
const screenshotPath = path.join(outputDirectory, "deployed-outbox-replay.png");
const browserErrors = [];

const { stdout } = await execFileAsync("gcloud", [
  "run", "services", "describe", agentService,
  `--project=${project}`,
  `--region=${region}`,
  "--format=json",
]);
const service = JSON.parse(stdout);
const container = service?.spec?.template?.spec?.containers?.[0];
const outboundMode = container?.env?.find((entry) => entry.name === "WATERLINE_OUTBOUND_MODE")?.value;
assert.equal(outboundMode, "outbox", "Refusing attestation: deployed agent is not explicitly in outbox mode");

await mkdir(outputDirectory, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, colorScheme: "dark" });
const page = await context.newPage();
page.on("pageerror", (error) => browserErrors.push(error.message));
page.on("console", (message) => {
  if (message.type() === "error" && !message.text().includes("favicon")) browserErrors.push(message.text());
});

let missionId = null;
let receiptId = null;
let proofError = null;
try {
  await page.goto(appUrl, { waitUntil: "networkidle", timeout: 45_000 });
  await page.getByRole("heading", { name: "Waterline" }).waitFor();
  await page.getByRole("button", { name: "Brief this flight" }).click();
  await page.locator(".mission-chip span").first().waitFor({ timeout: 45_000 });
  missionId = await page.locator(".mission-chip span").first().textContent();
  assert.match(missionId ?? "", /^mission-[0-9a-f]{20}$/);

  const boundary = await Promise.race([
    page.getByText("Human authority required", { exact: true })
      .waitFor({ timeout: 180_000 }).then(() => "human"),
    page.getByText("Review required — dispatch held", { exact: true })
      .waitFor({ timeout: 180_000 }).then(() => "held"),
    page.locator(".verdict.no").first()
      .waitFor({ timeout: 180_000 }).then(() => "failed"),
  ]);
  if (boundary !== "human") {
    const detail = (await page.locator(".verdict.no").last().textContent().catch(() => "")) ||
      (await page.getByText("Review required — dispatch held", { exact: true }).textContent().catch(() => "")) ||
      "mission failed closed before pilot attestation";
    throw new Error(`Mission reached ${boundary} boundary: ${detail}`);
  }
  await page.locator(".markdown-brief").scrollIntoViewIfNeeded();
  await page.screenshot({ path: awaitingScreenshotPath, fullPage: false });

  await page.getByRole("button", { name: "Attest & send one demo handoff" }).click();
  await page.getByText("HANDOFF COMPLETED", { exact: true }).waitFor({ timeout: 90_000 });
  receiptId = (await page.getByText(/^receipt /).textContent())?.replace(/^receipt /, "") ?? null;
  assert.match(receiptId ?? "", /^[0-9a-f]{64}$/);
  await page.getByText("via outbox", { exact: false }).waitFor();

  await page.getByRole("button", { name: "Replay same command · prove no second SMS" }).click();
  await page.getByText("REPLAY SUPPRESSED · ORIGINAL RECEIPT RETURNED", { exact: true }).waitFor({ timeout: 60_000 });
  assert.equal((await page.getByText(/^receipt /).textContent())?.replace(/^receipt /, ""), receiptId);
  await page.screenshot({ path: screenshotPath, fullPage: false });
} catch (error) {
  proofError = error;
  await page.screenshot({ path: path.join(outputDirectory, "deployed-outbox-failure.png"), fullPage: false })
    .catch(() => undefined);
} finally {
  await context.close();
  await browser.close();
}

const report = {
  status: proofError || browserErrors.length ? "FAILED" : "PASSED",
  app_url: appUrl,
  project,
  region,
  agent_service: agentService,
  outbound_mode: outboundMode,
  agent_revision: service?.status?.latestReadyRevisionName,
  mission_id: missionId,
  receipt_id: receiptId,
  duplicate_suppressed: true,
  browser_errors: browserErrors,
  error: proofError instanceof Error ? proofError.message : proofError ? String(proofError) : null,
  verified_at: new Date().toISOString(),
  awaiting_screenshot: awaitingScreenshotPath,
  screenshot: screenshotPath,
};
await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
console.log(JSON.stringify(report, null, 2));
if (proofError) throw proofError;
assert.deepEqual(browserErrors, [], `Browser errors: ${browserErrors.join(" | ")}`);
