import assert from "node:assert/strict";
import test from "node:test";

import { NextRequest } from "next/server";

import { POST } from "../src/app/api/waterline/[...path]/route";
import { signRelayRequest } from "../src/lib/relay-auth";
import { PILOT_COOKIE, resolvePilotSession } from "../src/lib/pilot-session";

test("the same-origin route forwards only a normalized signed server command", async (t) => {
  const priorAgent = process.env.WATERLINE_AGENT_URL;
  const priorSecret = process.env.WATERLINE_RELAY_SECRET;
  const originalFetch = globalThis.fetch;
  const secret = "test-route-relay-secret-with-more-than-32-bytes";
  let captured: { url: string; init: RequestInit } | undefined;

  process.env.WATERLINE_AGENT_URL = "http://127.0.0.1:8088";
  process.env.WATERLINE_RELAY_SECRET = secret;
  globalThis.fetch = async (input, init) => {
    captured = { url: String(input), init: init ?? {} };
    return new Response('data: {"type":"done"}\n\n', {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    if (priorAgent === undefined) delete process.env.WATERLINE_AGENT_URL;
    else process.env.WATERLINE_AGENT_URL = priorAgent;
    if (priorSecret === undefined) delete process.env.WATERLINE_RELAY_SECRET;
    else process.env.WATERLINE_RELAY_SECRET = priorSecret;
  });

  const request = new NextRequest("http://waterline.test/api/waterline/missions", {
    method: "POST",
    headers: { "content-type": "application/json", "x-waterline-actor": "pilot:browser" },
    body: JSON.stringify({
      cruise_alt_ft: 3500,
      destination: "Lady Evelyn Lake",
      departure: "cyyz",
    }),
  });
  const response = await POST(request, { params: Promise.resolve({ path: ["missions"] }) });

  assert.equal(response.status, 200);
  assert.ok(captured);
  assert.equal(captured.url, "http://127.0.0.1:8088/v1/missions");
  const body = String(captured.init.body);
  assert.equal(
    body,
    '{"departure":"CYYZ","destination":"Lady Evelyn Lake","cruise_alt_ft":3500}',
  );
  const headers = new Headers(captured.init.headers);
  const timestamp = headers.get("x-waterline-timestamp");
  const actor = headers.get("x-waterline-actor");
  assert.match(actor ?? "", /^pilot-session-[0-9a-f]{32}$/);
  assert.ok(timestamp);
  assert.equal(
    headers.get("x-waterline-signature"),
    signRelayRequest(
      secret,
      "POST",
      "/v1/missions",
      body,
      actor!,
      timestamp,
    ),
  );
  assert.equal(headers.get("authorization"), null);
  assert.equal(headers.get("x-serverless-authorization"), null);
  assert.match(response.headers.get("set-cookie") ?? "", /waterline_pilot=.*HttpOnly.*SameSite=Strict/);
});

test("pilot session cookies are owner-stable, expiring, and tamper-evident", () => {
  const secret = "test-pilot-session-secret-with-more-than-32-bytes";
  const created = resolvePilotSession(undefined, secret, false, 1_787_774_400);
  assert.ok(created.cookie);
  const value = created.cookie!.split(";", 1)[0].slice(`${PILOT_COOKIE}=`.length);

  const resumed = resolvePilotSession(value, secret, false, 1_787_774_430);
  assert.equal(resumed.actor, created.actor);
  assert.equal(resumed.cookie, undefined);

  const tampered = resolvePilotSession(
    `${value.slice(0, -1)}${value.endsWith("0") ? "1" : "0"}`,
    secret,
    false,
    1_787_774_430,
  );
  assert.notEqual(tampered.actor, created.actor);
  assert.ok(tampered.cookie);

  const expired = resolvePilotSession(value, secret, false, 1_788_379_201);
  assert.notEqual(expired.actor, created.actor);
});
