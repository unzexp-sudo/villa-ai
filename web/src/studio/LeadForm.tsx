// Lead capture form — wired to the real server-side gate.
//
// In dev REQUIRE_LEAD_GATE is OFF, so the server returns a dev_token and the
// form auto-verifies. In production the token is saved and sent as
// x-session-token on every POST /api/runs. No email provider is wired, so the
// "magic link" is logged server-side rather than sent.

import { useState } from "react";
import { lead } from "../lib/api";

type Stage = "form" | "verifying" | "done" | "error";

export function LeadForm({ onDone }: { onDone?: () => void }) {
  const [stage, setStage] = useState<Stage>("form");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("architect");
  const [company, setCompany] = useState("");
  const [country, setCountry] = useState("ID");
  const [consent, setConsent] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !email.trim() || !consent) return;
    setBusy(true);
    setError(null);
    try {
      const res = await lead.submit({
        name: name.trim(),
        email: email.trim(),
        role,
        company: company.trim(),
        country,
        consent,
      });
      // Dev mode: the server returns the token directly (no email sent).
      // Production: the token would arrive via a magic link email.
      const token = res.dev_token;
      if (token) {
        setStage("verifying");
        const verified = await lead.verify(token);
        lead.saveToken(verified.session_token);
        setStage("done");
        onDone?.();
      } else {
        // Production path: show "check your email" message.
        setStage("done");
      }
    } catch (e) {
      setError((e as Error).message);
      setStage("error");
    } finally {
      setBusy(false);
    }
  };

  if (stage === "done") {
    return (
      <div className="lead-done">
        <p>You're verified. The studio is unlocked.</p>
      </div>
    );
  }

  return (
    <form className="lead-form" onSubmit={submit}>
      <p className="eyebrow">Unlock the studio</p>
      <p className="muted small">
        Tell us who you are so we can save your runs. No password — we email a link.
      </p>

      {error && <p className="lead-error">{error}</p>}

      <label className="lead-field">
        <span>Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" required />
      </label>

      <label className="lead-field">
        <span>Work email</span>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@firm.com" required />
      </label>

      <div className="lead-row">
        <label className="lead-field">
          <span>Role</span>
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="architect">Architect</option>
            <option value="developer">Developer</option>
            <option value="builder">Builder</option>
            <option value="contractor">Contractor</option>
            <option value="other">Other</option>
          </select>
        </label>
        <label className="lead-field">
          <span>Country</span>
          <select value={country} onChange={(e) => setCountry(e.target.value)}>
            <option value="ID">Indonesia</option>
            <option value="VN">Vietnam</option>
            <option value="TH">Thailand</option>
            <option value="OTHER">Other</option>
          </select>
        </label>
      </div>

      <label className="lead-field">
        <span>Company (optional)</span>
        <input value={company} onChange={(e) => setCompany(e.target.value)} placeholder="Firm name" />
      </label>

      <label className="lead-consent">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} required />
        <span className="muted small">
          I agree to be contacted about this tool. My plan data is stored for my use and deleted on request.
        </span>
      </label>

      <button type="submit" className="btn-primary" disabled={busy || !name || !email || !consent}>
        {busy ? "Verifying…" : "Unlock the studio"}
      </button>
    </form>
  );
}
