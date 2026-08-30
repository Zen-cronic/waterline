export type FirebasePublicConfig = {
  apiKey: string;
  authDomain: string;
  projectId: string;
  storageBucket?: string;
  messagingSenderId: string;
  appId: string;
};

export function firebasePublicConfig(): FirebasePublicConfig {
  const values = {
    apiKey: process.env.FIREBASE_API_KEY,
    authDomain: process.env.FIREBASE_AUTH_DOMAIN,
    projectId: process.env.FIREBASE_PROJECT_ID ?? process.env.GOOGLE_CLOUD_PROJECT,
    storageBucket: process.env.FIREBASE_STORAGE_BUCKET,
    messagingSenderId: process.env.FIREBASE_MESSAGING_SENDER_ID,
    appId: process.env.FIREBASE_APP_ID,
  };
  const missing = Object.entries(values)
    .filter(([key, value]) => key !== "storageBucket" && !value)
    .map(([key]) => key);
  if (missing.length) throw new Error(`Firebase public configuration is incomplete: ${missing.join(", ")}`);
  return values as FirebasePublicConfig;
}
