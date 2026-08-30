import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test, { after, before } from "node:test";

import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
  type RulesTestEnvironment,
} from "@firebase/rules-unit-testing";
import {
  collection,
  deleteDoc,
  doc,
  getDoc,
  getDocs,
  limit,
  orderBy,
  query,
  serverTimestamp,
  setDoc,
  Timestamp,
  updateDoc,
} from "firebase/firestore";

const projectId = "waterline-rules-test";
const missionA = "mission-0123456789abcdefabcd";
const missionB = "mission-abcdef0123456789abcd";
const future = Timestamp.fromMillis(Date.now() + 3_600_000);
const past = Timestamp.fromMillis(Date.now() - 60_000);
let environment: RulesTestEnvironment;

before(async () => {
  environment = await initializeTestEnvironment({
    projectId,
    firestore: {
      host: "127.0.0.1",
      port: 8089,
      rules: readFileSync(new URL("../../firestore.rules", import.meta.url), "utf8"),
    },
  });
  await environment.withSecurityRulesDisabled(async (context) => {
    const store = context.firestore();
    for (const missionId of [missionA, missionB]) {
      await setDoc(doc(store, "handoff_threads", missionId), {
        missionId, status: "open", createdAt: Timestamp.now(), expiresAt: future,
        followerJoinedAt: null,
      });
    }
    await setDoc(doc(store, "handoff_threads", missionA, "members", "pilot-a"), {
      role: "pilot", expiresAt: future,
    });
    await setDoc(doc(store, "handoff_threads", missionA, "members", "follower-a"), {
      role: "follower", expiresAt: future,
    });
    await setDoc(doc(store, "handoff_threads", missionA, "members", "expired-a"), {
      role: "follower", expiresAt: past,
    });
    await setDoc(doc(store, "handoff_threads", missionB, "members", "pilot-b"), {
      role: "pilot", expiresAt: future,
    });
    await setDoc(doc(store, "handoff_threads", missionA, "messages", "seed"), {
      senderUid: "pilot-a", senderRole: "pilot", kind: "text", body: "Ready",
      clientMessageId: "seed-message", createdAt: Timestamp.now(), expiresAt: future,
    });
  });
});

after(async () => environment?.cleanup());

function message(overrides: Record<string, unknown> = {}) {
  return {
    senderUid: "pilot-a",
    senderRole: "pilot",
    kind: "text",
    body: "Route received",
    clientMessageId: crypto.randomUUID(),
    createdAt: serverTimestamp(),
    expiresAt: future,
    ...overrides,
  };
}

test("unauthenticated and unbound users cannot read a room", async () => {
  const guest = environment.unauthenticatedContext().firestore();
  const unbound = environment.authenticatedContext("unbound").firestore();
  await assertFails(getDoc(doc(guest, "handoff_threads", missionA)));
  await assertFails(getDoc(doc(unbound, "handoff_threads", missionA)));
});

test("clients cannot create thread or membership documents", async () => {
  const pilot = environment.authenticatedContext("pilot-a").firestore();
  await assertFails(setDoc(doc(pilot, "handoff_threads", "mission-new"), {
    missionId: "mission-new", status: "open", createdAt: serverTimestamp(), expiresAt: future,
    followerJoinedAt: null,
  }));
  await assertFails(setDoc(
    doc(pilot, "handoff_threads", missionA, "members", "extra-member"),
    { role: "follower", expiresAt: future },
  ));
});

test("pilot and follower can read only their same-room history", async () => {
  for (const uid of ["pilot-a", "follower-a"]) {
    const store = environment.authenticatedContext(uid).firestore();
    const result = await assertSucceeds(getDocs(query(
      collection(store, "handoff_threads", missionA, "messages"),
      orderBy("createdAt", "desc"),
      limit(50),
    )));
    assert.equal(result.size, 1);
  }
  const pilot = environment.authenticatedContext("pilot-a").firestore();
  await assertFails(getDocs(query(
    collection(pilot, "handoff_threads", missionA, "messages"),
    orderBy("createdAt", "desc"),
  )));
  const crossMission = environment.authenticatedContext("pilot-b").firestore();
  await assertFails(getDoc(doc(crossMission, "handoff_threads", missionA, "messages", "seed")));
});

test("expired membership and UID or role spoofing are denied", async () => {
  const expired = environment.authenticatedContext("expired-a").firestore();
  await assertFails(getDoc(doc(expired, "handoff_threads", missionA)));

  const pilot = environment.authenticatedContext("pilot-a").firestore();
  await assertFails(setDoc(
    doc(pilot, "handoff_threads", missionA, "messages", "spoof-uid"),
    message({ senderUid: "follower-a" }),
  ));
  await assertFails(setDoc(
    doc(pilot, "handoff_threads", missionA, "messages", "spoof-role"),
    message({ senderRole: "follower" }),
  ));
});

test("schema, size, update, and delete guards fail closed", async () => {
  const pilot = environment.authenticatedContext("pilot-a").firestore();
  const base = collection(pilot, "handoff_threads", missionA, "messages");
  await assertFails(setDoc(doc(base, "oversized"), message({ body: "x".repeat(501) })));
  await assertFails(setDoc(doc(base, "extra"), message({ unexpected: true })));
  await assertFails(updateDoc(doc(base, "seed"), { body: "changed" }));
  await assertFails(deleteDoc(doc(base, "seed")));
});

test("fixed acknowledgement succeeds and arbitrary acknowledgement fails", async () => {
  const follower = environment.authenticatedContext("follower-a").firestore();
  const base = collection(follower, "handoff_threads", missionA, "messages");
  await assertSucceeds(setDoc(doc(base, "ack-ok"), message({
    senderUid: "follower-a", senderRole: "follower", kind: "ack",
    body: "Following acknowledged",
  })));
  await assertFails(setDoc(doc(base, "ack-bad"), message({
    senderUid: "follower-a", senderRole: "follower", kind: "ack",
    body: "Sure",
  })));
});
