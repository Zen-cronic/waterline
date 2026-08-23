import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  output: "standalone",
  // Agent service base URL (Cloud Run in prod, local uvicorn in dev)
  env: { NEXT_PUBLIC_AGENT_URL: process.env.NEXT_PUBLIC_AGENT_URL ?? "http://127.0.0.1:8088" },
};
export default nextConfig;
