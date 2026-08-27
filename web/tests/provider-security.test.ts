import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";
import { NextRequest } from "next/server";
import twilio from "twilio";

import { POST as providerCallback } from "../src/app/api/waterline/providers/twilio/status/route";
import { verifyHandoffToken } from "../src/lib/handoff-token";
import { verifyAndNormalizeTwilioStatus } from "../src/lib/twilio-webhook";

test("Twilio callback signature is verified before bounded fields cross the relay", () => {
  const authToken = "test-twilio-auth-token";
  const url = "https://waterline.test/api/waterline/providers/twilio/status";
  const params = {
    MessageSid: `SM${"a".repeat(32)}`,
    MessageStatus: "delivered",
    ErrorCode: "",
    FutureTwilioField: "preserved-for-signature-validation",
  };
  const encodedBody = new URLSearchParams(params).toString();
  const signature = twilio.getExpectedTwilioSignature(authToken, url, params);
  assert.deepEqual(
    verifyAndNormalizeTwilioStatus({ authToken, signature, url, encodedBody }),
    {
      provider_reference: params.MessageSid,
      provider_status: "delivered",
      error_code: null,
    },
  );
  assert.throws(
    () => verifyAndNormalizeTwilioStatus({
      authToken, signature, url: `${url}/tampered`, encodedBody,
    }),
    /INVALID_TWILIO_SIGNATURE/,
  );
});

test("handoff summary is signed, bounded, and expires", () => {
  const secret = "handoff-test-secret-with-at-least-32-bytes";
  const payload = {
    v: 1,
    mission_id: "mission-0123456789abcdefabcd",
    departure: "CYYZ",
    destination: "Lady Evelyn Lake",
    landing_sector: "west",
    eta: "18:40Z",
    expires_at: 1_787_778_000,
  };
  const encoded = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const signature = createHmac("sha256", secret).update(encoded).digest("hex");
  const token = `${encoded}.${signature}`;
  assert.equal(verifyHandoffToken(token, secret, 1_787_774_400)?.landing_sector, "west");
  assert.equal(verifyHandoffToken(token, secret, 1_787_778_001), null);
  assert.equal(verifyHandoffToken(`${encoded}.${"0".repeat(64)}`, secret, 1_787_774_400), null);
});

test("public provider route validates the canonical URL then uses a distinct private relay identity", async (t) => {
  const originalFetch = globalThis.fetch;
  const prior = {
    agent: process.env.WATERLINE_AGENT_URL,
    relay: process.env.WATERLINE_RELAY_SECRET,
    twilio: process.env.TWILIO_AUTH_TOKEN,
    publicUrl: process.env.WATERLINE_PUBLIC_WEB_URL,
  };
  const authToken = "test-twilio-auth-token";
  const publicUrl = "https://waterline.example.test";
  const callbackUrl = `${publicUrl}/api/waterline/providers/twilio/status`;
  const params = { MessageSid: `SM${"b".repeat(32)}`, MessageStatus: "delivered" };
  const encodedBody = new URLSearchParams(params).toString();
  const signature = twilio.getExpectedTwilioSignature(authToken, callbackUrl, params);
  let captured: { url: string; init?: RequestInit } | undefined;
  process.env.WATERLINE_AGENT_URL = "http://127.0.0.1:8088";
  process.env.WATERLINE_RELAY_SECRET = "provider-route-relay-secret-with-more-than-32-bytes";
  process.env.TWILIO_AUTH_TOKEN = authToken;
  process.env.WATERLINE_PUBLIC_WEB_URL = publicUrl;
  globalThis.fetch = async (input, init) => {
    captured = { url: String(input), init };
    return Response.json({ provider_status: "delivered" });
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    for (const [name, value] of Object.entries({
      WATERLINE_AGENT_URL: prior.agent,
      WATERLINE_RELAY_SECRET: prior.relay,
      TWILIO_AUTH_TOKEN: prior.twilio,
      WATERLINE_PUBLIC_WEB_URL: prior.publicUrl,
    })) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });

  const request = new NextRequest(
    "http://internal:8080/api/waterline/providers/twilio/status",
    {
      method: "POST",
      headers: {
        "content-type": "application/x-www-form-urlencoded",
        "x-twilio-signature": signature,
      },
      body: encodedBody,
    },
  );
  const response = await providerCallback(request);
  assert.equal(response.status, 204);
  assert.equal(captured?.url, "http://127.0.0.1:8088/v1/providers/twilio/status");
  const headers = new Headers(captured?.init?.headers);
  assert.equal(headers.get("x-waterline-actor"), "provider:twilio-status");
  assert.deepEqual(JSON.parse(String(captured?.init?.body)), {
    provider_reference: params.MessageSid,
    provider_status: "delivered",
    error_code: null,
  });
});
