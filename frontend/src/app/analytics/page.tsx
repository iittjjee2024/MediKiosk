"use client";

/**
 * Analytics.
 *
 * Deliberately surfaces the metrics that can embarrass the platform --
 * physician edit rate, grounding pass rate, withheld facts, SLA breaches --
 * because a dashboard that can only show success is not a measurement system.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import * as api from "@/lib/api";
import { LoginCard, useStaff } from "@/components/StaffLogin";

type Data = Awaited<ReturnType<typeof api.dashboard>>;

export default function AnalyticsPage() {
  const { staff, ready, signIn, signOut } = useStaff();
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!staff) return;
    try {
      setData(await api.dashboard(staff.token));
      setError(null);
    } catch (err) {
      setError(err instanceof api.ApiError ? err.message : "Load failed");
    }
  }, [staff]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 10000);
    return () => clearInterval(id);
  }, [refresh]);

  if (!ready) return null;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          MediKiosk
          <small>Analytics &middot; including the uncomfortable metrics</small>
        </div>
        <div className="row">
          <Link href="/" style={{ color: "#fff", fontSize: "0.85rem" }}>
            Home
          </Link>
          {staff && (
            <button className="btn ghost" style={{ minHeight: 40 }} onClick={signOut}>
              Sign out
            </button>
          )}
        </div>
      </header>

      <main className="main stack">
        {!staff ? (
          <LoginCard onSignedIn={signIn} hint="Any staff account works here." />
        ) : (
          <>
            {error && <div className="banner critical">{error}</div>}
            {data && (
              <>
                <Group
                  title="Intake"
                  note="Completeness excludes fields that do not apply, so a simple presentation is not penalised."
                  metrics={[
                    ["Sessions", data.sessions.total],
                    ["Completed", data.sessions.completed],
                    ["In progress", data.sessions.in_progress],
                    ["Escalated", data.sessions.escalated],
                    ["Mean completeness", pct(data.sessions.mean_completeness)],
                  ]}
                />
                <Group
                  title="Facts"
                  note="Withheld items are perception outputs that were too uncertain to admit. They went to a human instead of into the record."
                  metrics={[
                    ["Total facts", data.facts.total],
                    ["Marked unconfirmed", data.facts.unconfirmed],
                    ["Conflicting", data.facts.conflicting],
                    ["Withheld for human", data.facts.withheld_pending_human],
                  ]}
                />
                <Group
                  title="Quality"
                  note="Physician edit rate is monitored, not targeted: it locates the weakest extraction paths."
                  metrics={[
                    [
                      "Mean grounding pass rate",
                      pct(data.quality.mean_grounding_pass_rate),
                    ],
                    [
                      "Physician edit rate",
                      pct(data.quality.physician_edit_rate_pct),
                    ],
                    [
                      "Physician reject rate",
                      pct(data.quality.physician_reject_rate_pct),
                    ],
                  ]}
                />
                <Group
                  title="Triage"
                  note="An alert nobody sees is equivalent to no alert, so acknowledgement is measured."
                  metrics={[
                    ["Alerts raised", data.triage.alerts_total],
                    ["Acknowledged", data.triage.acknowledged],
                    ["SLA adherence", pct(data.triage.sla_adherence_pct)],
                    ["Still open", data.triage.open],
                  ]}
                />
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? "no data yet" : `${value}%`;
}

function Group({
  title,
  note,
  metrics,
}: {
  title: string;
  note: string;
  metrics: [string, string | number | null][];
}) {
  return (
    <section className="card stack">
      <h2 style={{ margin: 0 }}>{title}</h2>
      <p className="small muted">{note}</p>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
          gap: "0.9rem",
        }}
      >
        {metrics.map(([label, value]) => (
          <div
            key={label}
            style={{
              border: "1px solid var(--line)",
              borderRadius: 12,
              padding: "0.9rem",
              background: "var(--pale)",
            }}
          >
            <div className="small muted">{label}</div>
            <div style={{ fontSize: "1.7rem", fontWeight: 800 }}>
              {value ?? "\u2014"}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
