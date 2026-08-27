import assert from "node:assert/strict";
import test from "node:test";

import { signRelayRequest } from "../src/lib/relay-auth";
import { authorizeRelayRequest, RelayPolicyError } from "../src/lib/relay-policy";

test("mission creation strips browser identity and normalizes the exact command", () => {
  const decision = authorizeRelayRequest({
    method: "POST",
    path: ["missions"],
    search: "",
    body: JSON.stringify({ departure: "cyyz", destination: "Lady Evelyn Lake", cruise_alt_ft: 3500 }),
  });
  assert.equal(decision.action, "mission.create");
  assert.deepEqual(JSON.parse(decision.upstreamBody), {
    departure: "CYYZ",
    destination: "Lady Evelyn Lake",
    cruise_alt_ft: 3500,
  });
});

test("browser-selected actor, session, or recipient on initial intake fails closed", () => {
  assert.throws(
    () => authorizeRelayRequest({
      method: "POST",
      path: ["missions"],
      search: "",
      body: JSON.stringify({
        departure: "CYYZ",
        destination: "Lady Evelyn Lake",
        cruise_alt_ft: 3500,
        actor: "pilot:attacker",
        session_id: "stolen",
        responsible_email: "attacker@example.com",
      }),
    }),
    (error: unknown) => error instanceof RelayPolicyError && error.code === "UNEXPECTED_FIELDS",
  );
});

test("only explicit attestation command reaches the resume endpoint", () => {
  const missionId = "mission-0123456789abcdefabcd";
  const decision = authorizeRelayRequest({
    method: "POST",
    path: ["missions", missionId, "attest"],
    search: "",
    body: JSON.stringify({
      confirm_dispatch: true,
      eta: "16:00Z",
      grace_min: 60,
    }),
  });
  assert.equal(decision.action, "mission.pilot-attest");
  assert.equal(decision.upstreamPath, `/v1/missions/${missionId}/attest`);
  assert.deepEqual(JSON.parse(decision.upstreamBody), {
    confirm_dispatch: true,
    eta: "16:00Z",
    grace_min: 60,
  });

  assert.throws(
    () => authorizeRelayRequest({
      method: "POST",
      path: ["missions", missionId, "attest"],
      search: "",
      body: JSON.stringify({
        confirm_dispatch: true,
        responsible_email: "attacker@example.com",
        eta: "16:00Z",
        grace_min: 60,
      }),
    }),
    (error: unknown) => error instanceof RelayPolicyError && error.code === "UNEXPECTED_FIELDS",
  );

  assert.throws(
    () => authorizeRelayRequest({ method: "POST", path: ["internal", "send"], search: "", body: "{}" }),
    (error: unknown) => error instanceof RelayPolicyError && error.code === "COMMAND_NOT_ALLOWED",
  );

  assert.throws(
    () => authorizeRelayRequest({
      method: "POST",
      path: ["missions", missionId, "attest"],
      search: "",
      body: JSON.stringify({
        confirm_dispatch: true,
        eta: "16:00Z\nINJECTED SMS TEXT",
        grace_min: 60,
      }),
    }),
    (error: unknown) => error instanceof RelayPolicyError && error.code === "INVALID_ETA",
  );
});

test("restore and recovery are exact owner-bound routes", () => {
  const missionId = "mission-0123456789abcdefabcd";
  const restore = authorizeRelayRequest({
    method: "GET", path: ["missions", missionId], search: "", body: "",
  });
  assert.equal(restore.action, "mission.restore");
  assert.equal(restore.upstreamPath, `/v1/missions/${missionId}`);

  const resume = authorizeRelayRequest({
    method: "POST",
    path: ["missions", missionId, "resume"],
    search: "",
    body: JSON.stringify({ confirm_resume: true }),
  });
  assert.equal(resume.action, "mission.resume");
  assert.equal(resume.upstreamPath, `/v1/missions/${missionId}/resume`);

  assert.throws(
    () => authorizeRelayRequest({
      method: "POST", path: ["missions", missionId, "resume"], search: "",
      body: JSON.stringify({ confirm_resume: false }),
    }),
    (error: unknown) => error instanceof RelayPolicyError &&
      error.code === "RESUME_CONFIRMATION_REQUIRED",
  );
});

test("relay signature changes with actor, path, or body", () => {
  const secret = "a-secure-relay-secret-with-more-than-32-bytes";
  const timestamp = "1787774400";
  const body = '{"departure":"CYYZ"}';
  const base = signRelayRequest(secret, "POST", "/v1/missions", body, "pilot:one", timestamp);
  assert.notEqual(base, signRelayRequest(secret, "POST", "/v1/missions", body, "pilot:two", timestamp));
  assert.notEqual(base, signRelayRequest(secret, "POST", "/v1/other", body, "pilot:one", timestamp));
  assert.notEqual(base, signRelayRequest(secret, "POST", "/v1/missions", body + " ", "pilot:one", timestamp));
});
