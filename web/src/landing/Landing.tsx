// TEMPORARY ORCHESTRATOR PLACEHOLDER — the real landing page is owned by M7.
// Exists only so the route works and `npm run typecheck` can run while M7 lands.
// M7 may overwrite this file wholesale; nothing here is a product decision.

export default function Landing() {
  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "48px 22px" }}>
      <h1>Villa AI</h1>
      <p className="muted">
        Plan coordination for villas and apartments. The landing page is owned by the
        M7 module and is not part of the interior feature.
      </p>
      <p><a href="/app">Open the MEP app &rarr;</a></p>
      <p><a href="/interior">Interior 3D walkthrough &rarr;</a></p>
    </main>
  );
}
