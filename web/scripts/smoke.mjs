import { chromium } from "playwright";
const URL = process.env.SMOKE_URL || "http://127.0.0.1:3010/";
const OUT = process.env.SMOKE_OUT || "/tmp/waterline_smoke.png";
const errors = [];
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push("PAGEERROR: " + e.message));
await page.goto(URL, { waitUntil: "networkidle" });
await page.click("text=Brief this flight");
// wait for at least the corridor filter step + a briefing to appear
let steps = 0, brief = "";
for (let i = 0; i < 60; i++) {
  steps = await page.locator(".step").count();
  brief = (await page.locator(".brief").first().textContent().catch(() => "")) || "";
  const verdict = await page.locator(".verdict").count();
  if (brief && verdict) break;
  await page.waitForTimeout(1500);
}
// let the map settle + tiles paint
await page.waitForTimeout(2500);
const canvas = await page.locator("#map canvas").count();
const verdictText = (await page.locator(".verdict").first().textContent().catch(() => "")) || "";
await page.screenshot({ path: OUT, fullPage: false });
console.log(JSON.stringify({
  steps, mapCanvas: canvas, briefLen: brief.length,
  verdict: verdictText.slice(0, 80), consoleErrors: errors.slice(0, 5),
}, null, 2));
await browser.close();
