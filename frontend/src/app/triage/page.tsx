"use client";

/**
 * Triage console.
 *
 * The engine raises alerts; it never reorders the queue. A human assesses each
 * one, and the SLA timer records whether they did -- an alert nobody sees is
 * equivalent to no alert.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import * as api from "@/lib/api";
import type { TriageAlert } from "@/lib/api";
import { LoginCard, useStaff } from "@/components/StaffLogin";

export default function TriagePage() {
  const { staff, ready, signIn, signOut } = useStaff();
  const [alerts, setAlerts] = useState<TriageAlert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    if (!staff) return;
    try {
      setAlerts((await api.triageAlerts(staff.token)).alerts);
      setError(null);
    } catch (err) {
      setError(err instanceof api.ApiError ? err.message : "Load failed");
    }
  }, [staff]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  if (!ready) return null;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          MediKiosk
          <small>Triage console &middot; deterministic red-flag rules</small>
        </div>
        <div className="row">
          <Link href="/" style={{ color: "#fff", fontSize: "0.85rem" }}>
            Home
          </Link>
          {staff && <span className="pill info">{staff.full_name}</span>}
          {staff && (
            <button className="btn ghost" style={{ minHeight: 40 }} onClick={signOut}>
              Sign out
            </button>
          )}
        </div>
      </header>

      <main className="main stack">
        {!staff ? (
          <LoginCard onSignedIn={signIn} hint="Use nurse.devi for triage." />
        ) : (
          <>
            {error && <div className="banner critical">{error}</div>}

            <section className="card stack">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h2 style={{ margin: 0 }}>Open alerts</h2>
                <span className={`pill ${alerts.length ? "bad" : "ok"}`}>
                  {alerts.length} open
                </span>
              </div>
              <p className="small muted">
                Sensitivity is prioritised over specificity: a false alert costs
                a brief assessment, a missed alert can cost a life.
              </p>

              {alerts.length === 0 && (
                <p className="muted small">
                  No open alerts. Rule evaluations are still logged for every
                  answer, including non-firing ones, so sensitivity can be
                  analysed retrospectively.
                </p>
              )}

              <div className="stack">
                {alerts.map((a) => (
                  <div
                    key={a.id}
                    className={`banner ${
                      a.severity === "critical" ? "critical" : "warn"
                    } stack`}
                  >
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <div className="row">
                        <span
                          className={`pill ${
                            a.severity === "critical" ? "bad" : "warn"
                          }`}
                        >
                          {a.severity}
                        </span>
                        <strong>{a.patient.name}</strong>
                        <span className="mono small">{a.rule_code}</span>
                        <span className="small muted">
                          rule v{a.rule_version}
                        </span>
                      </div>
                      {a.sla_breached && (
                        <span className="pill bad">SLA breached</span>
                      )}
                    </div>

                    <div className="small muted">
                      Triggered by:{" "}
                      <span className="mono">
                        {a.triggering_facts.refs.join(", ") || "n/a"}
                      </span>
                    </div>
                    {a.triggering_facts.errored && (
                      <div className="small">
                        <strong>
                          Raised by a rule-evaluation error (fail-safe).
                        </strong>{" "}
                        Assess the patient directly.
                      </div>
                    )}
                    <div className="small muted">
                      SLA deadline: {a.sla_deadline ?? "n/a"}
                    </div>

                    <div className="row">
                      <button
                        className="btn"
                        disabled={busy || a.status === "acknowledged"}
                        onClick={async () => {
                          setBusy(true);
                          try {
                            await api.acknowledgeAlert(
                              staff.token,
                              a.id,
                              "Assessed by triage staff",
                            );
                            await refresh();
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        {a.status === "acknowledged"
                          ? "Acknowledged"
                          : "Acknowledge"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
