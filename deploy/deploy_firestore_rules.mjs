import { readFile } from "node:fs/promises";
import path from "node:path";

const project = process.env.WL_PROJECT_ID ?? "ata-2026-waterline";
const token = process.env.WL_ACCESS_TOKEN;
if (!token) throw new Error("WL_ACCESS_TOKEN is required");

const root = path.resolve(import.meta.dirname, "..");
const content = await readFile(path.join(root, "firestore.rules"), "utf8");
const headers = {
  authorization: `Bearer ${token}`,
  "content-type": "application/json",
  "x-goog-user-project": project,
};

async function request(url, init = {}) {
  const response = await fetch(url, { ...init, headers: { ...headers, ...init.headers } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${JSON.stringify(body)}`);
  return body;
}

const ruleset = await request(
  `https://firebaserules.googleapis.com/v1/projects/${project}/rulesets`,
  {
    method: "POST",
    body: JSON.stringify({ source: { files: [{ name: "firestore.rules", content }] } }),
  },
);
const releaseName = `projects/${project}/releases/cloud.firestore`;
const current = await fetch(`https://firebaserules.googleapis.com/v1/${releaseName}`, { headers });
if (current.status === 404) {
  await request(`https://firebaserules.googleapis.com/v1/projects/${project}/releases`, {
    method: "POST",
    body: JSON.stringify({ name: releaseName, rulesetName: ruleset.name }),
  });
} else {
  if (!current.ok) throw new Error(`Could not inspect ${releaseName}: ${current.status}`);
  await request(`https://firebaserules.googleapis.com/v1/${releaseName}`, {
    method: "PATCH",
    body: JSON.stringify({
      release: { name: releaseName, rulesetName: ruleset.name },
      updateMask: "rulesetName",
    }),
  });
}
process.stdout.write(`Firestore rules released from ${ruleset.name}\n`);
