import { mkdir } from "node:fs/promises";
import path from "node:path";

import { chromium } from "playwright";

const appUrl = process.env.APP_URL ?? "http://127.0.0.1:3010";
const outputDirectory = path.resolve(process.env.THEME_PREVIEW_OUTPUT ?? "../.playwright-mcp/theme-preview");
await mkdir(outputDirectory, { recursive: true });

const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, colorScheme: "dark" });
  const page = await context.newPage();
  await page.goto(appUrl, { waitUntil: "networkidle", timeout: 45_000 });
  await page.getByRole("heading", { name: "Waterline" }).waitFor();
  await page.screenshot({ path: path.join(outputDirectory, "waterline-dark.png") });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
  await page.locator(".maplibregl-canvas").waitFor();
  await page.waitForTimeout(1_500);
  await page.screenshot({ path: path.join(outputDirectory, "waterline-light.png") });
  console.log(`theme previews: ${outputDirectory}`);
} finally {
  await browser.close();
}
