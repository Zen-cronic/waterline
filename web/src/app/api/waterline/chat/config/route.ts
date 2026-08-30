import { firebasePublicConfig } from "@/lib/firebase-config";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(): Promise<Response> {
  try {
    return Response.json(firebasePublicConfig(), {
      headers: { "cache-control": "no-store", "x-content-type-options": "nosniff" },
    });
  } catch {
    return Response.json(
      { detail: "Follower room configuration is unavailable" },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
}
