import type { NextRequest } from "next/server";

import { forwardAgentCommand } from "@/lib/agent-command";
import { verifyAndNormalizeTwilioStatus } from "@/lib/twilio-webhook";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: NextRequest): Promise<Response> {
  const authToken = process.env.TWILIO_AUTH_TOKEN;
  const signature = request.headers.get("x-twilio-signature") ?? "";
  if (!authToken || !signature) {
    return Response.json({ detail: "Provider signature unavailable" }, { status: 401 });
  }
  const encodedBody = await request.text();
  if (Buffer.byteLength(encodedBody) > 16_384) {
    return Response.json({ detail: "Provider callback too large" }, { status: 413 });
  }
  let normalized;
  try {
    const publicOrigin = process.env.WATERLINE_PUBLIC_WEB_URL?.replace(/\/$/, "");
    const signatureUrl = publicOrigin
      ? `${publicOrigin}${request.nextUrl.pathname}`
      : request.nextUrl.toString();
    normalized = verifyAndNormalizeTwilioStatus({
      authToken,
      signature,
      url: signatureUrl,
      encodedBody,
    });
  } catch {
    return Response.json({ detail: "Provider signature or status is invalid" }, { status: 401 });
  }
  const body = JSON.stringify(normalized);
  const upstream = await forwardAgentCommand({
    method: "POST",
    path: "/v1/providers/twilio/status",
    body,
    actor: "provider:twilio-status",
  });
  if (!upstream.ok) {
    return Response.json({ detail: "Provider receipt was not accepted" }, { status: upstream.status });
  }
  return new Response(null, { status: 204, headers: { "cache-control": "no-store" } });
}
