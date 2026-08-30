import type { NextRequest } from "next/server";

import { bearerToken, bindFollowerToRoom, ChatSessionError } from "@/lib/chat-session";
import { firebaseAdminAuth, firebaseAdminStore } from "@/lib/firebase-admin";
import { verifyHandoffToken } from "@/lib/handoff-token";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function fail(status: number, detail: string): Response {
  return Response.json({ detail }, { status, headers: { "cache-control": "no-store" } });
}

export async function POST(request: NextRequest): Promise<Response> {
  try {
    const payload = await request.json() as Record<string, unknown>;
    if (
      Object.keys(payload).join(",") !== "handoffToken" ||
      typeof payload.handoffToken !== "string"
    ) return fail(400, "handoffToken is required");
    const handoff = verifyHandoffToken(
      payload.handoffToken,
      process.env.WATERLINE_HANDOFF_SECRET ?? "",
    );
    if (!handoff) return fail(403, "Follower-room capability is invalid or expired");
    const idToken = bearerToken(request.headers.get("authorization"));
    const decoded = await firebaseAdminAuth().verifyIdToken(idToken);
    const session = await bindFollowerToRoom(firebaseAdminStore(), decoded.uid, handoff);
    return Response.json(
      { missionId: handoff.mission_id, ...session },
      { headers: { "cache-control": "no-store" } },
    );
  } catch (error) {
    if (error instanceof ChatSessionError) return fail(error.status, error.message);
    return fail(503, "Follower room is unavailable");
  }
}
