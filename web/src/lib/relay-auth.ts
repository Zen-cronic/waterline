import { createHash, createHmac } from "node:crypto";

export function signRelayRequest(
  secret: string,
  method: string,
  path: string,
  body: string,
  actor: string,
  timestamp: string,
): string {
  const digest = createHash("sha256").update(body, "utf8").digest("hex");
  const canonical = `${timestamp}\n${method.toUpperCase()}\n${path}\n${digest}\n${actor}`;
  return createHmac("sha256", secret).update(canonical, "utf8").digest("hex");
}
