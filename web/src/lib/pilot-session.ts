import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const PILOT_COOKIE = "waterline_pilot";
export const PILOT_SESSION_SECONDS = 7 * 24 * 60 * 60;

type PilotSession = { actor: string; cookie?: string };

function signature(secret: string, actor: string, issuedAt: string): string {
  return createHmac("sha256", secret)
    .update(`waterline-pilot-session-v1\n${actor}\n${issuedAt}`, "utf8")
    .digest("hex");
}

function validCookie(value: string | undefined, secret: string, now: number): string | undefined {
  if (!value) return undefined;
  const [actor, issuedAt, supplied] = value.split(".");
  if (
    !/^pilot-session-[0-9a-f]{32}$/.test(actor ?? "") ||
    !/^\d{10}$/.test(issuedAt ?? "") ||
    !/^[0-9a-f]{64}$/.test(supplied ?? "")
  ) return undefined;
  const age = now - Number(issuedAt);
  if (age < -60 || age > PILOT_SESSION_SECONDS) return undefined;
  const expected = signature(secret, actor, issuedAt);
  if (!timingSafeEqual(Buffer.from(expected, "hex"), Buffer.from(supplied, "hex"))) {
    return undefined;
  }
  return actor;
}

export function resolvePilotSession(
  value: string | undefined,
  secret: string,
  secure: boolean,
  now = Math.floor(Date.now() / 1000),
): PilotSession {
  const existing = validCookie(value, secret, now);
  if (existing) return { actor: existing };

  const actor = `pilot-session-${randomBytes(16).toString("hex")}`;
  const issuedAt = String(now);
  const encoded = `${actor}.${issuedAt}.${signature(secret, actor, issuedAt)}`;
  const cookie = [
    `${PILOT_COOKIE}=${encoded}`,
    "Path=/api/waterline",
    "HttpOnly",
    "SameSite=Strict",
    `Max-Age=${PILOT_SESSION_SECONDS}`,
    secure ? "Secure" : "",
  ].filter(Boolean).join("; ");
  return { actor, cookie };
}
