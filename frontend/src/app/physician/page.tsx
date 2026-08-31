"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import * as api from "@/lib/api";
import type { CitedSentence, SummaryView, WorklistRow } from "@/lib/api";
import { LoginCard, useStaff } from "@/components/StaffLogin";

export default function PhysicianPage() {
  const { staff, ready, signIn, signOut } = useStaff();
  const [rows, setRows] = useState<WorklistRow[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<SummaryView | null>(null);
  const [sentence, setSentence] = useState<CitedSentence | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approved, setApproved] = useState<{
    content_hash: string;
    facts: Record<string, number>;
    integration: Record<string, string>;
  } | null>(null);

  const refresh = useCallback(async () => {
    if (!staff) return;
    try {
      setRows((await api.worklist(staff.token)).sessions);
    } catch (err) {
      setError(err instanceof api.ApiError ? err.message : "Load failed");
    }
  }, [staff]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 8000);
    return () => clearInterval(id);
  }, [refresh]);

  const openSession = useCallback(
    async (sessionId: string) => {
      if (!staff) return;
      setBusy(true);
      setError(null);
      setApproved(null);
      setSentence(null);
      try {
        setSelected(sessionId);
        setView(await api.sessionSummary(staff.token, sessionId));
      } catch (err) {
        setError(err instanceof api.ApiError ? err.message : "Load failed");
      } finally {
        setBusy(false);
      }
    },
    [staff],
  );

  const act = useCallback(
    async (fn: () => Promise<unknown>) => {
      setBusy(true);
      setError(null);
      try {
        await fn();
        if (selected) await openSession(selected);
        await refresh();
      } catch (err) {
        setError(err instanceof api.ApiError ? err.message : "Action failed");
      } finally {
        setBusy(false);
      }
    },
    [openSession, refresh, selected],
  );

  if (!ready) return null;
  if (!staff) {
    return (
      <Shell onSignOut={signOut} who={null}>
        <LoginCard
          onSignedIn={signIn}
          hint="Use dr.rao for the general OPD or dr.iyer for AYUSH."
        />
      </Shell>
    );
  }

  const summaryId = view?.cited_summary.summary_id;
  const unresolvedConflicts = (view?.conflicts.length ?? 0) > 0;

  return (
    <Shell onSignOut={signOut} who={`${staff.full_name} (${staff.role})`}>
      {error && <div className="banner critical">{error}</div>}

      <div className="grid-2" style={{ alignItems: "start" }}>
        {}
        <section className="card stack">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2 style={{ margin: 0 }}>Worklist</h2>
            <span className="pill info">{rows.length} waiting</span>
          </div>
          <p className="small muted">
            Escalated sessions appear first: a triage alert outranks queue order.
          </p>
          <table className="table">
            <thead>
              <tr>
                <th>Patient</th>
                <th>Complete</th>
                <th>Alerts</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.session_id} data-escalated={r.status === "escalated"}>
                  <td>
                    <strong>{r.patient.name}</strong>
                    <div className="small muted">
                      {r.care_system} &middot; {r.department}
                      {r.patient.abha_linked && " \u00b7 ABHA"}
                    </div>
                  </td>
                  <td>{Math.round(r.completeness)}%</td>
                  <td>
                    {r.alerts.length === 0 ? (
                      <span className="small muted">&mdash;</span>
                    ) : (
                      r.alerts.map((a) => (
                        <span key={a.id} className="pill bad small">
                          {a.severity}
                        </span>
                      ))
                    )}
                  </td>
                  <td>
                    <button
                      className="btn secondary"
                      style={{ minHeight: 44, padding: "0.4rem 0.9rem" }}
                      onClick={() => openSession(r.session_id)}
                    >
                      Open
                    </button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted small">
                    No completed intake sessions yet. Run the patient kiosk
                    first.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>

        {}
        <section className="card stack">
          <h2 style={{ margin: 0 }}>Clinical history (draft)</h2>

          {!view && (
            <p className="muted small">
              Select a patient. The summary is already generated before they
              enter the room.
            </p>
          )}

          {view && (
            <>
              <div className="row">
                <span className="pill ok">
                  grounding {view.cited_summary.grounding_pass_rate}%
                </span>
                <span className="pill info">
                  v{view.cited_summary.version} &middot;{" "}
                  {view.cited_summary.status}
                </span>
                {view.pending_documents > 0 && (
                  <span className="pill warn">
                    {view.pending_documents} document(s) processing
                  </span>
                )}
                {!view.interaction_check_performed && (
                  <span className="pill warn">no interaction check</span>
                )}
              </div>

              {view.needs_human_verification.length > 0 && (
                <div className="banner warn small stack">
                  <strong>
                    {view.needs_human_verification.length} item(s) withheld as
                    too uncertain to record
                  </strong>
                  {view.needs_human_verification.map((v) => (
                    <div key={v.id} className="mono">
                      {v.field_code}: &ldquo;{v.candidate_text}&rdquo; (
                      {v.confidence.toFixed(2)} &middot; {v.reason})
                    </div>
                  ))}
                </div>
              )}

              {unresolvedConflicts && (
                <div className="banner critical small stack">
                  <strong>Flagged conflicts &mdash; not auto-resolved</strong>
                  {view.conflicts.map((c) => (
                    <div key={c.fact_id}>
                      {c.label}: {c.value}{" "}
                      <span className="muted">({c.source_type})</span>
                    </div>
                  ))}
                </div>
              )}

              <div className="stack" style={{ gap: "1rem" }}>
                {view.body.order
                  .filter((s) => view.body.sections[s])
                  .map((section) => (
                    <div key={section}>
                      <h3>{view.body.labels[section] ?? section}</h3>
                      {view.cited_summary.sentences
                        .filter((s) => s.section === section)
                        .map((s) => (
                          <div
                            key={s.sentence_id}
                            className="sentence"
                            data-selected={
                              sentence?.sentence_id === s.sentence_id
                            }
                            onClick={() => setSentence(s)}
                            role="button"
                            tabIndex={0}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") setSentence(s);
                            }}
                          >
                            {s.text}
                            {s.citations.map((c, i) => (
                              <span key={c.fact_id} className="cite">
                                {i + 1}
                              </span>
                            ))}
                          </div>
                        ))}
                    </div>
                  ))}
              </div>

              <div className="row" style={{ marginTop: "0.5rem" }}>
                <button
                  className="btn"
                  disabled={busy || !summaryId}
                  onClick={() =>
                    act(async () => {
                      const res = await api.approveSummary(
                        staff.token,
                        summaryId!,
                        true,
                      );
                      setApproved(res);
                    })
                  }
                >
                  {"\u2714"} Approve &amp; export
                </button>
                <button
                  className="btn ghost"
                  disabled={busy || !summaryId}
                  onClick={() =>
                    act(() =>
                      api.fhirBundle(staff.token, selected!).then((b) => {
                        const w = window.open("", "_blank");
                        w?.document.write(
                          `<pre>${JSON.stringify(b, null, 2)}</pre>`,
                        );
                      }),
                    )
                  }
                >
                  View FHIR bundle
                </button>
              </div>

              {approved && (
                <div className="banner info small stack">
                  <strong>Final clinical record created</strong>
                  <div className="mono">{approved.content_hash}</div>
                  <div>
                    accepted {approved.facts.accepted} &middot; edited{" "}
                    {approved.facts.edited} &middot; rejected{" "}
                    {approved.facts.rejected}
                  </div>
                  <div>
                    {Object.entries(approved.integration).map(([k, v]) => (
                      <span key={k} className="pill info small">
                        {k}: {v}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </section>
      </div>

      {}
      {sentence && (
        <section className="card stack" style={{ marginTop: "1.25rem" }}>
          <h2 style={{ margin: 0 }}>Where this came from</h2>
          <p className="small muted">
            Every fact records its source, extraction method, model version,
            confidence and timestamp. A sentence that cannot cite is removed
            before publication.
          </p>
          <blockquote
            style={{
              margin: 0,
              padding: "0.75rem 1rem",
              borderLeft: "4px solid var(--deep)",
              background: "var(--pale)",
            }}
          >
            {sentence.text}
          </blockquote>

          {sentence.citations.length === 0 && (
            <p className="small muted">
              Structural statement &mdash; asserts nothing about the patient, so
              it carries no citation.
            </p>
          )}

          {sentence.citations.map((c, i) => (
            <div key={c.fact_id} className="stack" style={{ gap: "0.5rem" }}>
              <div className="row">
                <span className="cite">{i + 1}</span>
                <strong>
                  {c.label}: {c.value}
                </strong>
                <span className="pill info small">{c.category}</span>
                {c.verification_status === "unconfirmed" && (
                  <span className="pill warn small">unconfirmed</span>
                )}
                {c.is_conflicting && (
                  <span className="pill bad small">conflicting</span>
                )}
                <span className="pill info small">{c.physician_status}</span>
              </div>

              {c.provenance && (
                <dl className="provenance">
                  <dt>source</dt>
                  <dd>{c.provenance.source_type}</dd>
                  <dt>method</dt>
                  <dd>{c.provenance.extraction_method}</dd>
                  <dt>model</dt>
                  <dd>
                    {c.provenance.model_name ?? "n/a"}
                    {c.provenance.model_version
                      ? ` @ ${c.provenance.model_version}`
                      : ""}
                  </dd>
                  <dt>confidence</dt>
                  <dd>{c.provenance.confidence.toFixed(3)}</dd>
                  <dt>captured</dt>
                  <dd>{c.provenance.captured_at}</dd>
                  <dt>device</dt>
                  <dd>{c.provenance.device_id ?? "n/a"}</dd>
                  {c.provenance.document_id && (
                    <>
                      <dt>document</dt>
                      <dd>
                        {c.provenance.document_id} p.
                        {c.provenance.page_number}
                      </dd>
                    </>
                  )}
                </dl>
              )}

              <div className="row">
                <button
                  className="btn secondary"
                  style={{ minHeight: 44, padding: "0.4rem 0.9rem" }}
                  disabled={busy}
                  onClick={() => {
                    const next = window.prompt("Corrected value", c.value ?? "");
                    if (next && summaryId) {
                      act(() =>
                        api.editFact(staff.token, summaryId, c.fact_id, next),
                      );
                    }
                  }}
                >
                  Edit fact
                </button>
                <button
                  className="btn danger"
                  style={{ minHeight: 44, padding: "0.4rem 0.9rem" }}
                  disabled={busy}
                  onClick={() =>
                    summaryId &&
                    act(() =>
                      api.rejectFact(
                        staff.token,
                        summaryId,
                        c.fact_id,
                        "rejected at review",
                      ),
                    )
                  }
                >
                  Reject fact
                </button>
              </div>
            </div>
          ))}
        </section>
      )}
    </Shell>
  );
}

function Shell({
  children,
  who,
  onSignOut,
}: {
  children: React.ReactNode;
  who: string | null;
  onSignOut: () => void;
}) {
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          MediKiosk
          <small>Physician dashboard &middot; draft for review</small>
        </div>
        <div className="row">
          <Link href="/" style={{ color: "#fff", fontSize: "0.85rem" }}>
            Home
          </Link>
          {who && <span className="pill info">{who}</span>}
          {who && (
            <button className="btn ghost" style={{ minHeight: 40 }} onClick={onSignOut}>
              Sign out
            </button>
          )}
        </div>
      </header>
      <main className="main stack">{children}</main>
    </div>
  );
}
