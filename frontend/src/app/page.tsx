import Link from "next/link";

export default function Home() {
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          MediKiosk
          <small>
            Pre-consultation clinical history &middot; deterministic engines
            &middot; cited facts
          </small>
        </div>
        <span className="pill info">MVP</span>
      </header>

      <main className="main stack">
        <section className="card">
          <h1>Choose a station</h1>
          <p className="muted">
            The patient station runs the intake interview. The clinical stations
            consume what it produces.
          </p>
        </section>

        <div className="grid-2">
          <Link href="/kiosk" className="card stack" style={{ textDecoration: "none", color: "inherit" }}>
            <h2>Patient kiosk</h2>
            <p className="muted">
              Language, audio-explained consent, then the adaptive interview by
              voice or touch. Runs with no network.
            </p>
            <span className="pill info">Patient</span>
          </Link>

          <Link href="/physician" className="card stack" style={{ textDecoration: "none", color: "inherit" }}>
            <h2>Physician dashboard</h2>
            <p className="muted">
              Worklist, cited summary, provenance viewer, and the approval gate.
              Nothing enters the record without it.
            </p>
            <span className="pill info">Physician</span>
          </Link>

          <Link href="/triage" className="card stack" style={{ textDecoration: "none", color: "inherit" }}>
            <h2>Triage console</h2>
            <p className="muted">
              Deterministic red-flag alerts with SLA timers. The engine alerts;
              a human decides.
            </p>
            <span className="pill info">Nurse</span>
          </Link>

          <Link href="/analytics" className="card stack" style={{ textDecoration: "none", color: "inherit" }}>
            <h2>Analytics</h2>
            <p className="muted">
              Completeness, grounding pass rate, physician edit rate, SLA
              adherence. Including the metrics that expose weakness.
            </p>
            <span className="pill info">Admin</span>
          </Link>
        </div>

        <section className="card">
          <h3>Demo credentials</h3>
          <p className="small muted" style={{ marginBottom: "0.5rem" }}>
            Password for every account: <code className="mono">medikiosk-demo</code>
          </p>
          <table className="table">
            <thead>
              <tr>
                <th>Username</th>
                <th>Role</th>
                <th>Sees</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="mono">dr.rao</td>
                <td>physician</td>
                <td>Worklist, cited summaries, approval</td>
              </tr>
              <tr>
                <td className="mono">dr.iyer</td>
                <td>ayush_practitioner</td>
                <td>Same, for AYUSH sessions</td>
              </tr>
              <tr>
                <td className="mono">nurse.devi</td>
                <td>nurse</td>
                <td>Triage alerts only</td>
              </tr>
              <tr>
                <td className="mono">admin.it</td>
                <td>it_admin</td>
                <td>Operations. No clinical data by design</td>
              </tr>
              <tr>
                <td className="mono">officer.privacy</td>
                <td>privacy_officer</td>
                <td>Audit chain. No clinical content by design</td>
              </tr>
            </tbody>
          </table>
        </section>
      </main>
    </div>
  );
}
