import { applicationDefault, getApps, initializeApp } from "firebase-admin/app";
import { getAuth } from "firebase-admin/auth";
import { getFirestore } from "firebase-admin/firestore";

import { firebasePublicConfig } from "@/lib/firebase-config";

function adminApp() {
  if (getApps().length) return getApps()[0]!;
  const projectId = firebasePublicConfig().projectId;
  return initializeApp({ credential: applicationDefault(), projectId });
}

export function firebaseAdminAuth() {
  return getAuth(adminApp());
}

export function firebaseAdminStore() {
  return getFirestore(adminApp());
}
