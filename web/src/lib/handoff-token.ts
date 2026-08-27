import { createHmac, timingSafeEqual } from "node:crypto";

export type HandoffSummary = {
  v: 1;
  mission_id: string;
  departure: string;
  destination: string;
  landing_sector: string;
  eta: string;
  expires_at: number;
};

function signature(secret: string, encoded: string): Buffer {
  return Buffer.from(createHmac("sha256", secret).update(encoded).digest("hex"), "ascii");
}

export function verifyHandoffToken(
  token: string,
  secret: string,
  nowSeconds = Math.floor(Date.now() / 1000),
): HandoffSummary | null {
  if (Buffer.byteLength(secret) < 32) return null;
  const separator = token.lastIndexOf(".");
  if (separator < 1) return null;
  const encoded = token.slice(0, separator);
  const supplied = Buffer.from(token.slice(separator + 1), "ascii");
  const expected = signature(secret, encoded);
  if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) return null;
  let value: unknown;
  try {
    value = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
  } catch {
    return null;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const summary = value as Partial<HandoffSummary>;
  if (
    summary.v !== 1 ||
    typeof summary.mission_id !== "string" || !/^mission-[0-9a-f]{20}$/.test(summary.mission_id) ||
    typeof summary.departure !== "string" ||
    typeof summary.destination !== "string" ||
    typeof summary.landing_sector !== "string" ||
    typeof summary.eta !== "string" ||
    typeof summary.expires_at !== "number" || !Number.isSafeInteger(summary.expires_at) ||
    summary.expires_at < nowSeconds
  ) return null;
  return summary as HandoffSummary;
}
