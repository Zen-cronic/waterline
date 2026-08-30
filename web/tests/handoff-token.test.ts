import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import { verifyHandoffToken } from "../src/lib/handoff-token";

const secret = "test-handoff-secret-with-at-least-thirty-two-bytes";
const payload = {
  v: 1 as const,
  mission_id: "mission-0123456789abcdefabcd",
  departure: "CYYZ",
  destination: "Lady Evelyn Lake",
  landing_sector: "west",
  eta: "16:00Z",
  expires_at: 1_788_200_000,
};

function token(value = payload) {
  const encoded = Buffer.from(JSON.stringify(value)).toString("base64url");
  const signature = createHmac("sha256", secret).update(encoded).digest("hex");
  return `${encoded}.${signature}`;
}

test("signed handoff token rejects alteration and expiry", () => {
  assert.equal(verifyHandoffToken(token(), secret, payload.expires_at - 1)?.mission_id, payload.mission_id);
  const valid = token();
  assert.equal(verifyHandoffToken(`${valid.slice(0, -1)}0`, secret, payload.expires_at - 1), null);
  assert.equal(verifyHandoffToken(valid, secret, payload.expires_at), null);
  assert.equal(verifyHandoffToken(valid, "short", payload.expires_at - 1), null);
});
