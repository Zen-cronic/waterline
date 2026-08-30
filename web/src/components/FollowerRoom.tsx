"use client";

import { useEffect, useMemo, useState } from "react";
import { getApp, getApps, initializeApp } from "firebase/app";
import {
  browserSessionPersistence,
  getAuth,
  setPersistence,
  signInAnonymously,
} from "firebase/auth";
import {
  addDoc,
  collection,
  doc,
  getFirestore,
  limit,
  onSnapshot,
  orderBy,
  query,
  serverTimestamp,
  Timestamp,
  type Unsubscribe,
} from "firebase/firestore";
import { QRCodeSVG } from "qrcode.react";

import type { FirebasePublicConfig } from "@/lib/firebase-config";

type Role = "pilot" | "follower";
type Connection = "connecting" | "live" | "reconnecting" | "unavailable" | "expired";
type Message = {
  id: string;
  senderUid: string;
  senderRole: Role;
  kind: "text" | "ack";
  body: string;
  createdAt?: { toMillis(): number };
};

export function FollowerRoom(props: {
  role: Role;
  missionId: string;
  token: string;
  expiresAt: number;
  inviteUrl?: string;
  onAcknowledged?: (acknowledged: boolean) => void;
}) {
  const { role, missionId, token, expiresAt, inviteUrl, onAcknowledged } = props;
  const [connection, setConnection] = useState<Connection>("connecting");
  const [messages, setMessages] = useState<Message[]>([]);
  const [followerJoined, setFollowerJoined] = useState(false);
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);
  const [copyLabel, setCopyLabel] = useState("Copy follower link");
  const [error, setError] = useState("");
  const [secondsLeft, setSecondsLeft] = useState(() => Math.max(0, expiresAt - Math.floor(Date.now() / 1000)));

  const acknowledged = useMemo(
    () => messages.some((message) => message.kind === "ack" && message.senderRole === "follower"),
    [messages],
  );

  useEffect(() => onAcknowledged?.(acknowledged), [acknowledged, onAcknowledged]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      const remaining = Math.max(0, expiresAt - Math.floor(Date.now() / 1000));
      setSecondsLeft(remaining);
      if (remaining === 0) setConnection("expired");
    }, 1000);
    return () => window.clearInterval(timer);
  }, [expiresAt]);

  useEffect(() => {
    let active = true;
    const unsubscribes: Unsubscribe[] = [];
    const offline = () => setConnection((current) => current === "expired" ? current : "reconnecting");
    window.addEventListener("offline", offline);

    void (async () => {
      try {
        setConnection("connecting");
        const configResponse = await fetch("/api/waterline/chat/config", { cache: "no-store" });
        if (!configResponse.ok) throw new Error("Follower room configuration is unavailable");
        const config = await configResponse.json() as FirebasePublicConfig;
        const app = getApps().length ? getApp() : initializeApp(config);
        const auth = getAuth(app);
        await setPersistence(auth, browserSessionPersistence);
        const credential = auth.currentUser
          ? { user: auth.currentUser }
          : await signInAnonymously(auth);
        const idToken = await credential.user.getIdToken();
        const endpoint = role === "pilot" ? "pilot-session" : "follower-session";
        const authorization = await fetch(`/api/waterline/chat/${endpoint}`, {
          method: "POST",
          headers: { "authorization": `Bearer ${idToken}`, "content-type": "application/json" },
          body: JSON.stringify(role === "pilot"
            ? { missionId, handoffToken: token }
            : { handoffToken: token }),
        });
        if (!authorization.ok) {
          const result = await authorization.json().catch(() => ({})) as { detail?: string };
          throw new Error(result.detail ?? "Follower room authorization failed");
        }
        if (!active) return;
        const messagesQuery = query(
          collection(getFirestore(app), "handoff_threads", missionId, "messages"),
          orderBy("createdAt", "desc"),
          limit(50),
        );
        unsubscribes.push(onSnapshot(
          messagesQuery,
          { includeMetadataChanges: true },
          (snapshot) => {
            if (!active) return;
            setMessages(snapshot.docs.map((item) => ({
              id: item.id,
              ...item.data(),
            } as Message)).reverse());
            setConnection(snapshot.metadata.fromCache ? "reconnecting" : "live");
          },
          () => {
            if (!active) return;
            setConnection("unavailable");
            setError("FOLLOWER ROOM UNAVAILABLE");
          },
        ));
        unsubscribes.push(onSnapshot(
          doc(getFirestore(app), "handoff_threads", missionId),
          (snapshot) => {
            if (!active) return;
            setFollowerJoined(Boolean(snapshot.data()?.followerJoinedAt));
          },
          () => {
            if (!active) return;
            setConnection("unavailable");
            setError("FOLLOWER ROOM UNAVAILABLE");
          },
        ));
      } catch (caught) {
        if (!active) return;
        setConnection("unavailable");
        setError(caught instanceof Error ? caught.message : "FOLLOWER ROOM UNAVAILABLE");
      }
    })();

    return () => {
      active = false;
      unsubscribes.forEach((unsubscribe) => unsubscribe());
      window.removeEventListener("offline", offline);
    };
  }, [missionId, role, token]);

  async function send(kind: "text" | "ack", value: string) {
    if (connection !== "live" || secondsLeft === 0 || sending) return;
    const trimmed = value.trim();
    if (!trimmed || trimmed.length > 500) return;
    setSending(true);
    try {
      const app = getApp();
      const user = getAuth(app).currentUser;
      if (!user) throw new Error("Anonymous room identity is unavailable");
      await addDoc(collection(getFirestore(app), "handoff_threads", missionId, "messages"), {
        senderUid: user.uid,
        senderRole: role,
        kind,
        body: trimmed,
        clientMessageId: crypto.randomUUID(),
        createdAt: serverTimestamp(),
        expiresAt: Timestamp.fromMillis(expiresAt * 1000),
      });
      if (kind === "text") setBody("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Message was not sent");
    } finally {
      setSending(false);
    }
  }

  const minutes = Math.ceil(secondsLeft / 60);
  return (
    <section className={`follower-room role-${role}`} aria-label="Flight follower room">
      <header className="follower-room-head">
        <div><small>FLIGHT FOLLOWER</small><strong>{acknowledged ? "FOLLOWING ACTIVE" : "HANDOFF READY"}</strong></div>
        <span className={`connection connection-${connection}`}>{connection === "live" ? "LIVE VIA FIRESTORE" : connection.toUpperCase()}</span>
      </header>

      {role === "pilot" && inviteUrl && (
        <div className="invitation">
          <div className="qr-shell"><QRCodeSVG value={inviteUrl} size={152} level="M" /></div>
          <div>
            <strong>{acknowledged ? "Follower acknowledged" : followerJoined ? "FOLLOWER JOINED · WAITING FOR ACK" : "WAITING FOR FOLLOWER"}</strong>
            <p>Scan on the responsible person&apos;s phone. Possession grants this one-hour room only.</p>
            <button type="button" onClick={() => void (async () => {
              try {
                await navigator.clipboard.writeText(inviteUrl);
                setCopyLabel("Copied");
                window.setTimeout(() => setCopyLabel("Copy follower link"), 1500);
              } catch {
                setError("Follower link could not be copied");
              }
            })()}>{copyLabel}</button>
          </div>
        </div>
      )}

      <div className="room-meta">
        <span>{minutes > 0 ? `${minutes} min remaining` : "Room expired"}</span>
        <span>{role === "pilot" ? "Pilot" : "Follower"}</span>
        <span>Coordination only · cannot change route authority</span>
      </div>

      {role === "follower" && !acknowledged && (
        <button className="acknowledge-button" type="button"
          disabled={connection !== "live" || sending || secondsLeft === 0}
          onClick={() => void send("ack", "Following acknowledged")}>
          Acknowledge flight following
        </button>
      )}

      <div className="message-history" aria-live="polite">
        {messages.length === 0 && <p>No coordination messages yet.</p>}
        {messages.map((message) => (
          <article className={`message message-${message.senderRole}`} key={message.id}>
            <small>{message.senderRole}{message.kind === "ack" ? " · acknowledgement" : ""}</small>
            <span>{message.body}</span>
          </article>
        ))}
      </div>

      <form className="message-composer" onSubmit={(event) => {
        event.preventDefault();
        void send("text", body);
      }}>
        <input aria-label="Coordination message" maxLength={500} value={body}
          onChange={(event) => setBody(event.target.value)} placeholder="Short coordination message" />
        <button type="submit" disabled={connection !== "live" || sending || !body.trim() || secondsLeft === 0}>Send</button>
      </form>
      {error && <p className="room-error" role="alert">{error}</p>}
    </section>
  );
}
