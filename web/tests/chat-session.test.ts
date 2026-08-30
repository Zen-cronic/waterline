import assert from "node:assert/strict";
import test from "node:test";

import type { Firestore } from "firebase-admin/firestore";

import {
  assertOwnedMissionHandoff,
  bearerToken,
  bindFollowerToRoom,
  bindPilotToRoom,
  ChatSessionError,
} from "../src/lib/chat-session";

const handoff = {
  v: 1 as const,
  mission_id: "mission-0123456789abcdefabcd",
  departure: "CYYZ",
  destination: "Lady Evelyn Lake",
  landing_sector: "west",
  eta: "16:00Z",
  expires_at: 1_888_200_000,
};

class Ref {
  constructor(readonly path: string) {}
  collection(name: string) {
    return { doc: (id: string) => new Ref(`${this.path}/${name}/${id}`) };
  }
}

function fakeStore(seed: Record<string, Record<string, unknown>> = {}) {
  const rows = new Map(Object.entries(seed));
  const store = {
    collection(name: string) {
      return { doc: (id: string) => new Ref(`${name}/${id}`) };
    },
    async runTransaction(work: (transaction: unknown) => Promise<void>) {
      const transaction = {
        async get(ref: Ref) {
          return { exists: rows.has(ref.path), data: () => rows.get(ref.path) };
        },
        create(ref: Ref, value: Record<string, unknown>) {
          if (rows.has(ref.path)) throw new Error("already exists");
          rows.set(ref.path, value);
        },
        set(ref: Ref, value: Record<string, unknown>) { rows.set(ref.path, value); },
        update(ref: Ref, value: Record<string, unknown>) {
          rows.set(ref.path, { ...rows.get(ref.path), ...value });
        },
      };
      await work(transaction);
    },
  };
  return { store: store as unknown as Firestore, rows };
}

test("pilot binding is idempotent and conflicting roles fail closed", async () => {
  const { store, rows } = fakeStore();
  await bindPilotToRoom(store, "pilot-a", handoff);
  await bindPilotToRoom(store, "pilot-a", handoff);
  assert.equal(rows.get(`handoff_threads/${handoff.mission_id}/members/pilot-a`)?.role, "pilot");

  rows.set(`handoff_threads/${handoff.mission_id}/members/conflict`, {
    role: "follower",
    expiresAt: { toMillis: () => handoff.expires_at * 1000 },
  });
  await assert.rejects(
    bindPilotToRoom(store, "conflict", handoff),
    (error: unknown) => error instanceof ChatSessionError && error.status === 409,
  );
});

test("follower requires the pilot-created thread", async () => {
  const { store } = fakeStore();
  await assert.rejects(
    bindFollowerToRoom(store, "follower-a", handoff),
    (error: unknown) => error instanceof ChatSessionError && error.status === 409,
  );
});

test("follower binding rejects a Firebase identity already bound as pilot", async () => {
  const threadPath = `handoff_threads/${handoff.mission_id}`;
  const memberPath = `${threadPath}/members/shared-identity`;
  const { store } = fakeStore({
    [threadPath]: {
      missionId: handoff.mission_id,
      status: "open",
      expiresAt: { toMillis: () => handoff.expires_at * 1000 },
      followerJoinedAt: null,
    },
    [memberPath]: {
      role: "pilot",
      expiresAt: { toMillis: () => handoff.expires_at * 1000 },
    },
  });
  await assert.rejects(
    bindFollowerToRoom(store, "shared-identity", handoff),
    (error: unknown) => error instanceof ChatSessionError && error.status === 409,
  );
});

test("owner mismatch and invalid bearer token are explicit", () => {
  assert.throws(
    () => assertOwnedMissionHandoff(404, null, "token"),
    (error: unknown) => error instanceof ChatSessionError && error.status === 403,
  );
  assert.throws(
    () => assertOwnedMissionHandoff(200, { handoff: { token: "other" } }, "token"),
    (error: unknown) => error instanceof ChatSessionError && error.status === 409,
  );
  assert.equal(bearerToken("Bearer valid.token_value"), "valid.token_value");
  assert.throws(() => bearerToken(null), ChatSessionError);
});
