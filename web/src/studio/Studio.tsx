// TEMPORARY ORCHESTRATOR PLACEHOLDER — the real studio is owned by M8.
// Exists only so the route works and `npm run typecheck` can run while M8 lands.
// M8 may overwrite this file wholesale; nothing here is a product decision.

export default function Studio() {
  return (
    <main className="card" style={{ maxWidth: 960, margin: "32px auto", padding: 22 }}>
      <h2>MEP Studio</h2>
      <p className="muted">
        The five-agent MEP pipeline is owned by the M8 module and is untouched by the
        interior feature. This stub keeps the route live.
      </p>
      <p><a href="/interior">Interior 3D walkthrough &rarr;</a></p>
    </main>
  );
}
