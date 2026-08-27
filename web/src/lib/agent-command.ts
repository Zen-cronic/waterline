import { GoogleAuth } from "google-auth-library";

import { signRelayRequest } from "@/lib/relay-auth";

const localAgent = "http://127.0.0.1:8088";
const localSecret = "waterline-local-relay-secret-change-before-deploy";

export function agentConfig(): { url: URL; local: boolean; secret?: string } {
  const url = new URL(process.env.WATERLINE_AGENT_URL ?? localAgent);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("WATERLINE_AGENT_URL must use HTTP or HTTPS");
  }
  const local = url.hostname === "127.0.0.1" || url.hostname === "localhost";
  return {
    url,
    local,
    secret: process.env.WATERLINE_RELAY_SECRET ?? (local ? localSecret : undefined),
  };
}

export async function forwardAgentCommand(input: {
  method: "GET" | "POST";
  path: string;
  body: string;
  actor: string;
}): Promise<Response> {
  const agent = agentConfig();
  if (!agent.secret || Buffer.byteLength(agent.secret) < 32) {
    return Response.json(
      { detail: "Authenticated relay is not configured", code: "RELAY_UNAVAILABLE" },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const headers = new Headers({
    "x-waterline-actor": input.actor,
    "x-waterline-timestamp": timestamp,
    "x-waterline-signature": signRelayRequest(
      agent.secret, input.method, input.path, input.body, input.actor, timestamp,
    ),
  });
  if (input.body) headers.set("content-type", "application/json");

  if (!agent.local) {
    const audience = process.env.WATERLINE_AGENT_AUDIENCE ?? agent.url.origin;
    const auth = new GoogleAuth();
    const client = await auth.getIdTokenClient(audience);
    const identityHeaders = await client.getRequestHeaders(audience);
    const authorization = identityHeaders.get("authorization");
    if (!authorization) throw new Error("Google identity client returned no authorization header");
    headers.set("x-serverless-authorization", authorization);
  }

  return fetch(new URL(input.path, agent.url), {
    method: input.method,
    headers,
    body: input.body || undefined,
    cache: "no-store",
    redirect: "manual",
  });
}
