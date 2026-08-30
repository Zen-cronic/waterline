import { notFound } from "next/navigation";

import { FollowerRoom } from "@/components/FollowerRoom";
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
        <small>WATERLINE · FLIGHT FOLLOWING</small>
        <h1>{handoff.departure} → {handoff.destination}</h1>
        <p>Join the pilot&apos;s short-lived coordination room and acknowledge that you are following this flight.</p>
        <dl>
          <div><dt>Departure</dt><dd>{handoff.departure}</dd></div>
          <div><dt>Destination</dt><dd>{handoff.destination}</dd></div>
          <div><dt>Candidate sector</dt><dd>{handoff.landing_sector.toUpperCase()} COVE</dd></div>
          <div><dt>ETA</dt><dd>{handoff.eta}</dd></div>
        </dl>
        <FollowerRoom
          role="follower"
          missionId={handoff.mission_id}
          token={token}
          expiresAt={handoff.expires_at}
        />
      </section>
    </main>
  );
}
