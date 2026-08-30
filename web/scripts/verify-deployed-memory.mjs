import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { promisify } from "node:util";

import { chromium } from "playwright";

const execFileAsync = promisify(execFile);
const requiredApproval = "I_APPROVE_OUTBOX_MEMORY_PROOF";

if (process.env.WATERLINE_MEMORY_PROOF_APPROVED !== requiredApproval) {
  console.error("Deployed memory proof not started.");
  console.error("This creates two missions and one pilot attestation; the verified transport must be outbox.");
  console.error(`Set WATERLINE_MEMORY_PROOF_APPROVED=${requiredApproval} to authorize it.`);
  process.exit(3);
}

const appUrl = (process.env.APP_URL ?? "https://waterline-web-2hjaxuzova-uc.a.run.app").replace(/\/$/, "");
const project = process.env.WATERLINE_PROJECT ?? "ata-2026-waterline";
const region = process.env.WATERLINE_REGION ?? "us-central1";
const agentService = process.env.WATERLINE_AGENT_SERVICE ?? "waterline-agent";
const sqlConnection = process.env.WATERLINE_SQL_CONNECTION ?? `${project}:${region}:waterline-pg`;
const proxyPort = Number(process.env.WATERLINE_SQL_PROXY_PORT ?? "9470");
const outputDirectory = path.resolve(process.env.MEMORY_PROOF_OUTPUT ?? "../.playwright-mcp/deployed-memory");
const reportPath = path.join(outputDirectory, "deployed-memory-proof.json");
const screenshotPath = path.join(outputDirectory, "deployed-memory-flight-2.png");
const browserErrors = [];

function waitForPort(port, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const socket = net.createConnection({ host: "127.0.0.1", port });
      socket.once("connect", () => { socket.destroy(); resolve(); });
      socket.once("error", () => {
        socket.destroy();
        if (Date.now() >= deadline) reject(new Error("Cloud SQL proxy did not become ready"));
        else setTimeout(attempt, 150);
      });
    };
    attempt();
  });
}

function localDatabaseUrl(secret) {
  const match = secret.trim().match(/^postgresql:\/\/([^:]+):([^@]+)@\/([^?]+)\?/);
  assert(match, "Cloud SQL database secret has an unexpected shape");
  return `postgresql://${match[1]}:${match[2]}@127.0.0.1:${proxyPort}/${match[3]}`;
}

function collect(child) {
  let stdout = "";
  let stderr = "";
  child.stdout?.on("data", (chunk) => { stdout += chunk; });
  child.stderr?.on("data", (chunk) => { stderr += chunk; });
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code) => code === 0
      ? resolve({ stdout, stderr })
      : reject(new Error(`child process exited ${code}: ${stderr || stdout}`)));
  });
}

async function waitForHumanBoundary(page) {
  const boundary = await Promise.race([
    page.getByText("Human authority required", { exact: true })
      .waitFor({ timeout: 180_000 }).then(() => "human"),
    page.getByText("Review required — dispatch held", { exact: true })
      .waitFor({ timeout: 180_000 }).then(() => "held"),
    page.locator(".verdict.no").first()
      .waitFor({ timeout: 180_000 }).then(() => "failed"),
  ]);
  if (boundary !== "human") {
    const detail = await page.locator(".verdict.no").last().textContent().catch(() => boundary);
    throw new Error(`Mission reached ${boundary} boundary: ${detail}`);
  }
}

const { stdout: serviceJson } = await execFileAsync("gcloud", [
  "run", "services", "describe", agentService,
  `--project=${project}`, `--region=${region}`, "--format=json",
]);
const service = JSON.parse(serviceJson);
const outboundMode = service?.spec?.template?.spec?.containers?.[0]?.env
  ?.find((entry) => entry.name === "WATERLINE_OUTBOUND_MODE")?.value;
assert.equal(outboundMode, "outbox", "Refusing memory proof: deployed agent is not explicitly in outbox mode");

const { stdout: databaseSecret } = await execFileAsync("gcloud", [
  "secrets", "versions", "access", "latest",
  "--secret=waterline-database-url", `--project=${project}`,
]);
const proxy = spawn("/tmp/cloud-sql-proxy", [
  "--gcloud-auth", "--address=127.0.0.1", `--port=${proxyPort}`, sqlConnection,
], { stdio: ["ignore", "ignore", "pipe"] });
let proxyError = "";
proxy.stderr.on("data", (chunk) => { proxyError += chunk; });

await mkdir(outputDirectory, { recursive: true });
let browser;
let context;
let mutation;
let report;
try {
  await waitForPort(proxyPort);
  browser = await chromium.launch({ headless: true });
  context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, colorScheme: "dark" });
  const page = await context.newPage();
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().includes("favicon")) browserErrors.push(message.text());
  });

  await page.goto(appUrl, { waitUntil: "networkidle", timeout: 45_000 });
  await page.getByRole("button", { name: "Brief this flight" }).click();
  await waitForHumanBoundary(page);
  const firstMission = await page.locator(".mission-chip span").first().textContent();
  const ownerLine = await page.locator(".mission-chip small").first().textContent();
  const ownerRef = ownerLine?.replace(/^authenticated owner /, "");
  assert.match(firstMission ?? "", /^mission-[0-9a-f]{20}$/);
  assert.match(ownerRef ?? "", /^(?:owner|pilot)-[0-9a-f]+$/);
  const firstSteps = await page.locator(".step").allTextContents();
  assert(firstSteps.some((step) => step.includes("Gemini reads 14 of")), "flight 1 did not expose the 14-item Gemini budget");

  await page.getByRole("button", { name: "Attest & send one demo handoff" }).click();
  await page.getByText("HANDOFF COMPLETED", { exact: true }).waitFor({ timeout: 90_000 });
  await page.getByText("MEMORY WRITTEN", { exact: true }).waitFor({ timeout: 30_000 });

  mutation = spawn("poetry", [
    "-C", "agent", "run", "python", "../scripts/mutate_notam_for_memory_demo.py",
    "--owner-ref", ownerRef, "--timeout", "180",
  ], {
    cwd: process.cwd(),
    env: { ...process.env, DATABASE_URL: localDatabaseUrl(databaseSecret) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const mutationResult = collect(mutation);

  await page.getByRole("button", { name: "Brief this flight" }).click();
  await waitForHumanBoundary(page);
  const secondMission = await page.locator(".mission-chip span").first().textContent();
  assert.notEqual(secondMission, firstMission);
  const mutationOutput = await mutationResult;
  const secondSteps = await page.locator(".step").allTextContents();
  const reduction = secondSteps.find((step) => /\d+ → 23 after owner-scoped memory/.test(step));
  const changed = secondSteps.find((step) => step.includes("resurfaced at full weight"));
  const routing = secondSteps.find((step) => step.includes("Gemini reads 14 of 23"));
  assert(reduction, "flight 2 did not reduce unchanged owner memory to the 23-item safety floor");
  assert(changed, "the mutated NOTAM digest did not resurface at full weight");
  assert(routing, "flight 2 did not retain the 14-item Gemini budget");
  await page.screenshot({ path: screenshotPath, fullPage: false });

  report = {
    status: "PASSED",
    app_url: appUrl,
    agent_revision: service?.status?.latestReadyRevisionName,
    outbound_mode: outboundMode,
    owner_ref: ownerRef,
    first_mission_id: firstMission,
    second_mission_id: secondMission,
    first_model_route: firstSteps.find((step) => step.includes("Gemini reads 14 of")),
    second_memory_reduction: reduction,
    second_changed_source: changed,
    second_model_route: routing,
    mutation: mutationOutput.stdout.trim(),
    browser_errors: browserErrors,
    verified_at: new Date().toISOString(),
    screenshot: screenshotPath,
  };
  assert.deepEqual(browserErrors, [], `Browser errors: ${browserErrors.join(" | ")}`);
} catch (error) {
  report = {
    status: "FAILED",
    app_url: appUrl,
    agent_revision: service?.status?.latestReadyRevisionName,
    outbound_mode: outboundMode,
    browser_errors: browserErrors,
    error: error instanceof Error ? error.message : String(error),
    proxy_error: proxyError,
    verified_at: new Date().toISOString(),
  };
} finally {
  if (mutation && mutation.exitCode === null) mutation.kill("SIGTERM");
  await context?.close().catch(() => undefined);
  await browser?.close().catch(() => undefined);
  proxy.kill("SIGTERM");
}

await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
console.log(JSON.stringify(report, null, 2));
assert.equal(report.status, "PASSED", report.error ?? "deployed memory proof failed");
