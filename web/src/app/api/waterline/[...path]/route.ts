import type { NextRequest } from "next/server";

import { agentConfig, forwardAgentCommand } from "@/lib/agent-command";
import { authorizeRelayRequest, RelayPolicyError } from "@/lib/relay-policy";
import { PILOT_COOKIE, resolvePilotSession } from "@/lib/pilot-session";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = { params: Promise<{ path: string[] }> };
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

  const agent = agentConfig();
  const secret = agent.secret;
  if (!secret || Buffer.byteLength(secret) < 32) {
    return Response.json(
      { detail: "Authenticated pilot relay is not configured", code: "RELAY_UNAVAILABLE" },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
  const pilot = resolvePilotSession(
    request.cookies.get(PILOT_COOKIE)?.value,
    secret,
    !agent.local,
  );
  const actor = pilot.actor;

  const upstream = await forwardAgentCommand({
    method: request.method as "GET" | "POST",
    path: decision.upstreamPath,
    body: decision.upstreamBody,
    actor,
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
