import { Timestamp, type Firestore } from "firebase-admin/firestore";

import type { HandoffSummary } from "@/lib/handoff-token";

export type ChatRole = "pilot" | "follower";

export class ChatSessionError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "ChatSessionError";
  }
}

function refs(store: Firestore, missionId: string, uid: string) {
  const thread = store.collection("handoff_threads").doc(missionId);
  return { thread, member: thread.collection("members").doc(uid) };
}

export async function bindPilotToRoom(
  store: Firestore, uid: string, handoff: HandoffSummary,
): Promise<{ role: "pilot"; expiresAt: number }> {
  const { thread, member } = refs(store, handoff.mission_id, uid);
  const expiresAt = Timestamp.fromMillis(handoff.expires_at * 1000);
  await store.runTransaction(async (transaction) => {
    const [threadSnapshot, memberSnapshot] = await Promise.all([
      transaction.get(thread), transaction.get(member),
    ]);
    if (memberSnapshot.exists && memberSnapshot.data()?.role !== "pilot") {
      throw new ChatSessionError(409, "Firebase identity already has a conflicting room role");
    }
    if (threadSnapshot.exists) {
      const data = threadSnapshot.data();
      if (data?.missionId !== handoff.mission_id || data?.status !== "open") {
        throw new ChatSessionError(409, "Follower room state conflicts with the signed mission");
      }
      if (data.expiresAt?.toMillis?.() !== expiresAt.toMillis()) {
        throw new ChatSessionError(409, "Follower room expiry conflicts with the durable receipt");
      }
    } else {
      transaction.create(thread, {
        missionId: handoff.mission_id,
        status: "open",
        createdAt: Timestamp.now(),
        expiresAt,
        followerJoinedAt: null,
      });
    }
    transaction.set(member, { role: "pilot", expiresAt });
  });
  return { role: "pilot", expiresAt: handoff.expires_at };
}

export async function bindFollowerToRoom(
  store: Firestore, uid: string, handoff: HandoffSummary,
): Promise<{ role: "follower"; expiresAt: number }> {
  const { thread, member } = refs(store, handoff.mission_id, uid);
  const expiresAt = Timestamp.fromMillis(handoff.expires_at * 1000);
  await store.runTransaction(async (transaction) => {
    const [threadSnapshot, memberSnapshot] = await Promise.all([
      transaction.get(thread), transaction.get(member),
    ]);
    if (!threadSnapshot.exists) {
      throw new ChatSessionError(409, "Pilot has not opened this follower room yet");
    }
    const threadData = threadSnapshot.data();
    if (
      threadData?.missionId !== handoff.mission_id ||
      threadData?.status !== "open" ||
      threadData?.expiresAt?.toMillis?.() !== expiresAt.toMillis()
    ) {
      throw new ChatSessionError(409, "Follower room does not match the signed invitation");
    }
    if (memberSnapshot.exists && memberSnapshot.data()?.role !== "follower") {
      throw new ChatSessionError(409, "Firebase identity already has a conflicting room role");
    }
    transaction.set(member, { role: "follower", expiresAt });
    if (!threadData.followerJoinedAt) {
      transaction.update(thread, { followerJoinedAt: Timestamp.now() });
    }
  });
  return { role: "follower", expiresAt: handoff.expires_at };
}

export function bearerToken(header: string | null): string {
  const match = header?.match(/^Bearer ([A-Za-z0-9._~-]+)$/);
  if (!match) throw new ChatSessionError(401, "Firebase ID token is required");
  return match[1];
}

export function assertOwnedMissionHandoff(
  upstreamStatus: number,
  payload: { handoff?: { token?: string } } | null,
  token: string,
): void {
  if (upstreamStatus === 404) {
    throw new ChatSessionError(403, "Pilot does not own this terminal mission");
  }
  if (upstreamStatus < 200 || upstreamStatus >= 300) {
    throw new ChatSessionError(503, "Mission ownership could not be verified");
  }
  if (payload?.handoff?.token !== token) {
    throw new ChatSessionError(409, "Mission invitation does not match the durable handoff receipt");
  }
}
