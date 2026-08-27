import { notFound } from "next/navigation";

import { verifyHandoffToken } from "@/lib/handoff-token";

export default async function HandoffPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  const secret = process.env.WATERLINE_HANDOFF_SECRET ?? "";
  const handoff = verifyHandoffToken(token, secret);
  if (!handoff) notFound();

  return (
    <main className="handoff-page">
      <section className="handoff-card">
        <small>WATERLINE · MARKED SYNTHETIC HANDOFF</small>
        <h1>No active flight</h1>
        <p>This short-lived summary was generated for a hackathon demonstration.</p>
        <dl>
          <div><dt>Departure</dt><dd>{handoff.departure}</dd></div>
          <div><dt>Destination</dt><dd>{handoff.destination}</dd></div>
          <div><dt>Candidate sector</dt><dd>{handoff.landing_sector.toUpperCase()} COVE</dd></div>
          <div><dt>ETA</dt><dd>{handoff.eta}</dd></div>
        </dl>
        <code>{handoff.mission_id}</code>
        <strong>PILOT REVIEW REQUIRED</strong>
      </section>
    </main>
  );
}
