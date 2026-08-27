import { GoogleAuth } from "google-auth-library";
import type { NextRequest } from "next/server";

import { signRelayRequest } from "@/lib/relay-auth";
import { authorizeRelayRequest, RelayPolicyError } from "@/lib/relay-policy";
import { PILOT_COOKIE, resolvePilotSession } from "@/lib/pilot-session";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = { params: Promise<{ path: string[] }> };
const localAgent = "http://127.0.0.1:8088";
const localSecret = "waterline-local-relay-secret-change-before-deploy";

function agentUrl(): URL {
  const url = new URL(process.env.WATERLINE_AGENT_URL ?? localAgent);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("WATERLINE_AGENT_URL must use HTTP or HTTPS");
  }
  return url;
}

function isLocal(url: URL): boolean {
  return url.hostname === "127.0.0.1" || url.hostname === "localhost";
}

async function relay(request: NextRequest, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  const body = await request.text();
  let decision;
  try {
    decision = authorizeRelayRequest({
      method: request.method,
      path,
      search: request.nextUrl.search,
      body,
    });
  } catch (error) {
    if (error instanceof RelayPolicyError) {
      return Response.json(
        { detail: error.message, code: error.code },
        { status: error.status, headers: { "cache-control": "no-store" } },
      );
    }
    throw error;
  }

  const agent = agentUrl();
  const local = isLocal(agent);
  const secret = process.env.WATERLINE_RELAY_SECRET ?? (local ? localSecret : undefined);
  if (!secret || Buffer.byteLength(secret) < 32) {
    return Response.json(
      { detail: "Authenticated pilot relay is not configured", code: "RELAY_UNAVAILABLE" },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
  const pilot = resolvePilotSession(
    request.cookies.get(PILOT_COOKIE)?.value,
    secret,
    !local,
  );
  const actor = pilot.actor;

  const timestamp = Math.floor(Date.now() / 1000).toString();
  const headers = new Headers({
    "x-waterline-actor": actor,
    "x-waterline-timestamp": timestamp,
    "x-waterline-signature": signRelayRequest(
      secret,
      request.method,
      decision.upstreamPath,
      decision.upstreamBody,
      actor,
      timestamp,
    ),
  });
  if (decision.upstreamBody) headers.set("content-type", "application/json");

  if (!local) {
    const audience = process.env.WATERLINE_AGENT_AUDIENCE ?? agent.origin;
    const auth = new GoogleAuth();
    const client = await auth.getIdTokenClient(audience);
    const identityHeaders = await client.getRequestHeaders(audience);
    const authorization = identityHeaders.get("authorization");
    if (!authorization) throw new Error("Google identity client returned no authorization header");
    headers.set("x-serverless-authorization", authorization);
  }

  const target = new URL(decision.upstreamPath, agent);
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body: decision.upstreamBody || undefined,
    cache: "no-store",
    redirect: "manual",
  });
  const responseHeaders = new Headers({
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  const contentType = upstream.headers.get("content-type");
  if (contentType) responseHeaders.set("content-type", contentType);
  if (pilot.cookie) responseHeaders.set("set-cookie", pilot.cookie);
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export const POST = relay;
export const GET = relay;
