import twilio from "twilio";

const allowedStatuses = new Set([
  "accepted", "scheduled", "queued", "sending", "sent", "delivered",
  "undelivered", "failed", "canceled", "read",
]);

export type NormalizedTwilioStatus = {
  provider_reference: string;
  provider_status: string;
  error_code: string | null;
};

export function verifyAndNormalizeTwilioStatus(input: {
  authToken: string;
  signature: string;
  url: string;
  encodedBody: string;
}): NormalizedTwilioStatus {
  const form = new URLSearchParams(input.encodedBody);
  const params = Object.fromEntries(form.entries());
  if (!twilio.validateRequest(input.authToken, input.signature, input.url, params)) {
    throw new Error("INVALID_TWILIO_SIGNATURE");
  }
  const sid = form.get("MessageSid") ?? "";
  const status = (form.get("MessageStatus") ?? "").toLowerCase();
  const errorCode = form.get("ErrorCode") || null;
  if (!/^SM[0-9A-Za-z]{16,64}$/.test(sid) || !allowedStatuses.has(status)) {
    throw new Error("INVALID_TWILIO_STATUS");
  }
  if (errorCode && !/^[0-9]{1,10}$/.test(errorCode)) {
    throw new Error("INVALID_TWILIO_STATUS");
  }
  return {
    provider_reference: sid,
    provider_status: status,
    error_code: errorCode,
  };
}
