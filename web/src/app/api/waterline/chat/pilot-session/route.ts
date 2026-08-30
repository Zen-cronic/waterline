import type { NextRequest } from "next/server";

import { agentConfig, forwardAgentCommand } from "@/lib/agent-command";
import {
  assertOwnedMissionHandoff,
  bearerToken,
  bindPilotToRoom,
  ChatSessionError,
} from "@/lib/chat-session";
import { firebaseAdminAuth, firebaseAdminStore } from "@/lib/firebase-admin";
import { verifyHandoffToken } from "@/lib/handoff-token";
import { PILOT_COOKIE, resolvePilotSession } from "@/lib/pilot-session";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function fail(status: number, detail: string): Response {
  return Response.json({ detail }, { status, headers: { "cache-control": "no-store" } });
}

export async function POST(request: NextRequest): Promise<Response> {
  try {
    const payload = await request.json() as Record<string, unknown>;
    if (
      Object.keys(payload).sort().join(",") !== "handoffToken,missionId" ||
      typeof payload.missionId !== "string" ||
      typeof payload.handoffToken !== "string"
    ) return fail(400, "missionId and handoffToken are required");

    const secret = process.env.WATERLINE_HANDOFF_SECRET ?? "";
    const handoff = verifyHandoffToken(payload.handoffToken, secret);
    if (!handoff || handoff.mission_id !== payload.missionId) {
      return fail(403, "Follower-room capability is invalid or expired");
    }
    const idToken = bearerToken(request.headers.get("authorization"));
    const decoded = await firebaseAdminAuth().verifyIdToken(idToken);

    const agent = agentConfig();
    if (!agent.secret || Buffer.byteLength(agent.secret) < 32) {
      return fail(503, "Authenticated pilot relay is unavailable");
    }
    const pilot = resolvePilotSession(
      request.cookies.get(PILOT_COOKIE)?.value,
      agent.secret,
      !agent.local,
    );
    if (pilot.cookie) return fail(401, "Authenticated pilot session is required");
    const upstream = await forwardAgentCommand({
      method: "GET",
      path: `/v1/missions/${handoff.mission_id}`,
      body: "",
      actor: pilot.actor,
    });
    const restored = upstream.ok
      ? await upstream.json() as { handoff?: { token?: string } }
      : null;
    assertOwnedMissionHandoff(upstream.status, restored, payload.handoffToken);

    const session = await bindPilotToRoom(firebaseAdminStore(), decoded.uid, handoff);
    return Response.json(
      { missionId: handoff.mission_id, ...session },
      { headers: { "cache-control": "no-store" } },
    );
  } catch (error) {
    if (error instanceof ChatSessionError) return fail(error.status, error.message);
    return fail(503, "Follower room is unavailable");
  }
}
