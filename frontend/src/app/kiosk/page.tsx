"use client";

/**
 * Patient kiosk.
 *
 * Design constraints that drive every decision on this screen:
 *   - the patient may not read, so audio is a first-class path
 *   - the patient may never have used a touchscreen, so targets are large and
 *     there are few choices per screen
 *   - the hall may be too noisy for reliable speech, so touch is equivalent
 *   - the network may drop mid-interview, so answers queue locally and the
 *     patient is never shown a technical error
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import * as api from "@/lib/api";
import type { AnswerResult, Question } from "@/lib/api";
import {
  LANGUAGES,
  type Lang,
  optionIcon,
  optionLabel,
  prompt,
  speechLocale,
  t,
} from "@/lib/i18n";
import { clearAll, drain, loadState, pendingCount, saveState } from "@/lib/offline";
import { useListen, useSpeak } from "@/components/Speech";

type Stage =
  | "language"
  | "consent"
  | "declined"
  | "interview"
  | "alert"
  | "done";

type Scopes = {
  scope_interview: boolean;
  scope_documents: boolean;
  scope_abdm_share: boolean;
  scope_audio_retention: boolean;
};

const DEVICE_ID = "kiosk-web-01";

export default function KioskPage() {
  const [stage, setStage] = useState<Stage>("language");
  const [lang, setLang] = useState<Lang>("hi");
  const [careSystem, setCareSystem] = useState<"allopathic" | "ayush">(
    "allopathic",
  );
  const [token, setToken] = useState<string | null>(null);
  const [sessionToken, setSessionToken] = useState<string | null>(null);
  const [question, setQuestion] = useState<Question | null>(null);
  const [multi, setMulti] = useState<string[]>([]);
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [completeness, setCompleteness] = useState(0);
  const [busy, setBusy] = useState(false);
  const [online, setOnline] = useState(true);
  const [queued, setQueued] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<{
    heard: string;
    confidence: number;
  } | null>(null);

  const locale = speechLocale(lang);
  const { speak, stop: stopSpeaking, supported: canSpeak } = useSpeak(locale);
  const listen = useListen(locale);
  const liveRef = useRef<HTMLDivElement>(null);

  // ---------------------------------------------------- connectivity ------
  useEffect(() => {
    const sync = () => setOnline(navigator.onLine);
    sync();
    const onOnline = async () => {
      setOnline(true);
      await drain();
      setQueued(await pendingCount());
    };
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", () => setOnline(false));
    const id = setInterval(async () => setQueued(await pendingCount()), 3000);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", sync);
      clearInterval(id);
    };
  }, []);

  // resume an interrupted session after a device restart
  useEffect(() => {
    (async () => {
      const saved = await loadState<{ token: string; lang: Lang }>("session");
      if (saved?.token) {
        setSessionToken(saved.token);
        setLang(saved.lang);
        setStage("interview");
      }
    })();
  }, []);

  const promptText = useMemo(
    () => (question ? prompt(lang, question.prompt_key) : ""),
    [lang, question],
  );

  // read each new question aloud: the audio path must not require a tap
  useEffect(() => {
    if (stage === "interview" && promptText && canSpeak) speak(promptText);
  }, [promptText, stage, canSpeak, speak]);

  // ------------------------------------------------------------ actions ---

  const begin = useCallback(
    async (chosen: Lang) => {
      setLang(chosen);
      setBusy(true);
      try {
        const id = await api.resolveIdentity({
          display_name: "Kiosk Patient",
          language: chosen,
          care_system: careSystem,
          device_id: DEVICE_ID,
          channel: "kiosk",
        });
        setToken(id.token);
        setStage("consent");
      } catch {
        setNotice("Could not reach the server. Please ask staff for help.");
      } finally {
        setBusy(false);
      }
    },
    [careSystem],
  );

  const submitConsent = useCallback(
    async (scopes: Scopes) => {
      if (!token) return;
      setBusy(true);
      try {
        const res = await api.grantConsent(token, {
          ...scopes,
          language: lang,
          explained_via_audio: true,
        });
        if (!res.granted || !res.session_id) {
          setStage("declined");
          return;
        }
        const bound = await api.bindSessionToken(token, res.session_id);
        setSessionToken(bound.token);
        await saveState("session", { token: bound.token, lang });
        const q = await api.nextQuestion(bound.token);
        setQuestion(q);
        setStage("interview");
      } catch {
        setNotice("Could not save your consent. Please ask staff for help.");
      } finally {
        setBusy(false);
      }
    },
    [token, lang],
  );

  const applyResult = useCallback(
    async (res: AnswerResult) => {
      setResult(res);
      setCompleteness(res.completeness.score);
      setMulti([]);
      listen.reset();

      if (res.red_flag_fired) {
        // The patient is told to see staff. The engine has already alerted
        // them; it does not reorder the queue itself.
        setStage("alert");
        if (canSpeak) speak(t(lang, "ui.alert_body"));
        return;
      }
      if (res.next_question) {
        setQuestion(res.next_question);
        return;
      }
      await finish();
    },
    [canSpeak, lang, listen, speak],
  );

  const send = useCallback(
    async (body: api.AnswerBody) => {
      if (!sessionToken || !question) return;
      setBusy(true);
      stopSpeaking();
      try {
        if (!navigator.onLine) {
          // Offline: queue and advance locally. The patient sees no error.
          await api.queueAnswer(sessionToken, body);
          setQueued(await pendingCount());
          setNotice(null);
          const optimistic = await api
            .nextQuestion(sessionToken)
            .catch(() => null);
          setQuestion(optimistic);
          if (!optimistic) setStage("done");
          setMulti([]);
          return;
        }
        const res = await api.submitAnswer(sessionToken, body);
        await applyResult(res);
      } catch {
        await api.queueAnswer(sessionToken, body);
        setQueued(await pendingCount());
      } finally {
        setBusy(false);
      }
    },
    [applyResult, question, sessionToken, stopSpeaking],
  );

  const finish = useCallback(async () => {
    if (!sessionToken) return;
    setBusy(true);
    try {
      await drain();
      await api.finalise(sessionToken);
      await clearAll();
      setStage("done");
      if (canSpeak) speak(t(lang, "ui.done_body"));
    } catch {
      setStage("done");
    } finally {
      setBusy(false);
    }
  }, [canSpeak, lang, sessionToken, speak]);

  const answerTouch = (value: string) =>
    send({ field_code: question!.field_code, value, input_mode: "touch" });

  const answerMulti = () =>
    send({
      field_code: question!.field_code,
      selected_options: multi.length ? multi : ["none"],
      input_mode: "touch",
    });

  const skip = () =>
    send({
      field_code: question!.field_code,
      input_mode: "touch",
      skipped_reason: "not_applicable",
    });

  /** Map a spoken phrase onto a canonical option code.
   *  Deliberately conservative: if it does not match an offered option we do
   *  not guess. The patient confirms, or taps instead. */
  const matchSpoken = (transcript: string): string | null => {
    if (!question) return null;
    const said = transcript.toLowerCase().trim();
    if (!said) return null;
    for (const code of question.options) {
      const label = optionLabel(lang, code).toLowerCase();
      if (said === label || said.includes(label) || label.includes(said)) {
        return code;
      }
    }
    return null;
  };

  useEffect(() => {
    if (!listen.heard || !question) return;
    const { transcript, confidence } = listen.heard;
    const code = matchSpoken(transcript);
    if (!code) {
      setConfirming({ heard: transcript, confidence });
      return;
    }
    // Let the server's confidence gate decide admission -- the client never
    // makes that call itself.
    send({
      field_code: question.field_code,
      value: code,
      input_mode: "voice",
      raw_transcript: transcript,
      asr_confidence: confidence,
      nlu_confidence: confidence,
    });
    listen.reset();
  }, [listen.heard]); // eslint-disable-line react-hooks/exhaustive-deps

  // ------------------------------------------------------------- render ---

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          {t(lang, "ui.title")}
          <small>{t(lang, "ui.subtitle")}</small>
        </div>
        <div className="row">
          <span className={`pill ${online ? "ok" : "warn"}`}>
            {online ? t(lang, "ui.online") : t(lang, "ui.offline")}
          </span>
          {queued > 0 && (
            <span className="pill info">
              {queued} {t(lang, "ui.queued")}
            </span>
          )}
        </div>
      </header>

      <div aria-live="polite" className="sr-only" ref={liveRef}>
        {promptText}
      </div>

      <main className="main stack">
        {notice && <div className="banner warn">{notice}</div>}

        {/* ------------------------------------------------ language ----- */}
        {stage === "language" && (
          <section className="card stack">
            <h1>{t(lang, "ui.choose_language")}</h1>
            <div className="options">
              {LANGUAGES.map((l) => (
                <button
                  key={l.code}
                  className="option"
                  aria-pressed={lang === l.code}
                  onClick={() => {
                    setLang(l.code);
                    speak(t(l.code, "ui.choose_language"));
                  }}
                >
                  <span className="icon" aria-hidden>
                    {"\u{1F5E3}"}
                  </span>
                  <span>
                    {l.native}
                    <br />
                    <span className="small muted">{l.label}</span>
                  </span>
                  {lang === l.code && (
                    <span className="check" aria-hidden>
                      {"\u2714"}
                    </span>
                  )}
                </button>
              ))}
            </div>

            <div className="row" style={{ marginTop: "0.5rem" }}>
              <button
                className={`option ${careSystem === "allopathic" ? "" : ""}`}
                style={{ minHeight: 72, flex: 1 }}
                aria-pressed={careSystem === "allopathic"}
                onClick={() => setCareSystem("allopathic")}
              >
                <span className="icon" aria-hidden>
                  {"\u{1FA7A}"}
                </span>
                <span>General OPD</span>
              </button>
              <button
                className="option"
                style={{ minHeight: 72, flex: 1 }}
                aria-pressed={careSystem === "ayush"}
                onClick={() => setCareSystem("ayush")}
              >
                <span className="icon" aria-hidden>
                  {"\u{1F33F}"}
                </span>
                <span>AYUSH OPD</span>
              </button>
            </div>

            <button
              className="btn"
              disabled={busy}
              onClick={() => begin(lang)}
              style={{ marginTop: "0.5rem" }}
            >
              {t(lang, "ui.begin")}
            </button>
          </section>
        )}

        {/* ------------------------------------------------- consent ----- */}
        {stage === "consent" && (
          <ConsentPanel
            lang={lang}
            busy={busy}
            onSpeak={speak}
            onAccept={submitConsent}
            onDecline={() =>
              submitConsent({
                scope_interview: false,
                scope_documents: false,
                scope_abdm_share: false,
                scope_audio_retention: false,
              })
            }
          />
        )}

        {stage === "declined" && (
          <section className="card stack">
            <h1>{t(lang, "ui.declined_heading")}</h1>
            <p>{t(lang, "ui.declined_body")}</p>
            <span className="pill ok">Recorded &middot; no penalty</span>
          </section>
        )}

        {/* ----------------------------------------------- interview ----- */}
        {stage === "interview" && question && (
          <section className="card stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="pill info">
                {t(lang, "ui.completeness")} {Math.round(completeness)}%
              </span>
              {result?.superseded_previous && (
                <span className="pill warn">answer replaced</span>
              )}
            </div>
            <div className="progress" aria-hidden>
              <span style={{ width: `${completeness}%` }} />
            </div>

            <h1>{promptText}</h1>
            <p className="muted small">{t(lang, "ui.tap_or_speak")}</p>

            <div className="row">
              <button
                className="btn secondary"
                onClick={() => speak(promptText)}
                disabled={!canSpeak}
              >
                {"\u{1F50A}"} {t(lang, "ui.listen")}
              </button>
              {listen.supported && (
                <button
                  className="btn mic"
                  data-recording={listen.listening}
                  onPointerDown={listen.start}
                  onPointerUp={listen.stop}
                  onPointerLeave={listen.stop}
                >
                  {"\u{1F3A4}"}{" "}
                  {listen.listening ? t(lang, "ui.stop") : t(lang, "ui.speak")}
                </button>
              )}
            </div>

            {confirming && (
              <div className="banner warn stack">
                <strong>{t(lang, "ui.confirm_heading")}</strong>
                <p className="mono">&ldquo;{confirming.heard}&rdquo;</p>
                <p className="small muted">
                  Not matched to an option, so nothing was recorded. Please tap
                  your answer.
                </p>
                <button
                  className="btn secondary"
                  onClick={() => setConfirming(null)}
                >
                  {t(lang, "ui.confirm_no")}
                </button>
              </div>
            )}

            {question.answer_type === "scale" ? (
              <div className="scale">
                {question.options.map((o) => (
                  <button
                    key={o}
                    aria-pressed={false}
                    disabled={busy}
                    onClick={() => answerTouch(o)}
                  >
                    {o}
                  </button>
                ))}
              </div>
            ) : (
              <div className="options">
                {question.options.map((code) => {
                  const icon = optionIcon(code);
                  const selected = multi.includes(code);
                  return (
                    <button
                      key={code}
                      className="option"
                      aria-pressed={
                        question.answer_type === "multi" ? selected : false
                      }
                      disabled={busy}
                      onClick={() =>
                        question.answer_type === "multi"
                          ? setMulti((prev) =>
                              prev.includes(code)
                                ? prev.filter((c) => c !== code)
                                : [...prev, code],
                            )
                          : answerTouch(code)
                      }
                    >
                      {icon && (
                        <span className="icon" aria-hidden>
                          {icon}
                        </span>
                      )}
                      <span>{optionLabel(lang, code)}</span>
                      {question.answer_type === "multi" && selected && (
                        <span className="check" aria-hidden>
                          {"\u2714"}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            )}

            <div className="row">
              {question.answer_type === "multi" && (
                <button className="btn" disabled={busy} onClick={answerMulti}>
                  {"\u2713"} Confirm
                </button>
              )}
              <button className="btn ghost" disabled={busy} onClick={skip}>
                {t(lang, "ui.skip")}
              </button>
            </div>

            {result && result.fact_admitted === false && (
              <div className="banner info small">
                Last answer was not clear enough to record automatically. It has
                been sent to a staff member to check.
              </div>
            )}
          </section>
        )}

        {stage === "interview" && !question && (
          <section className="card stack">
            <h1>{t(lang, "ui.done_heading")}</h1>
            <button className="btn" onClick={finish} disabled={busy}>
              Finish
            </button>
          </section>
        )}

        {/* -------------------------------------------------- red flag --- */}
        {stage === "alert" && (
          <section className="card stack">
            <div className="banner critical stack">
              <h1 style={{ margin: 0 }}>{t(lang, "ui.alert_heading")}</h1>
              <p style={{ margin: 0 }}>{t(lang, "ui.alert_body")}</p>
            </div>
            {result?.alerts.map((a) => (
              <div key={a.id} className="row">
                <span className="pill bad">{a.severity}</span>
                <span className="mono small">{a.rule_code}</span>
                <span className="small muted">
                  rule v{a.rule_version} &middot; deterministic
                </span>
              </div>
            ))}
            <button
              className="btn"
              onClick={async () => {
                setStage("interview");
                if (sessionToken) {
                  setQuestion(await api.nextQuestion(sessionToken));
                }
              }}
            >
              Continue answering
            </button>
          </section>
        )}

        {/* ------------------------------------------------------ done --- */}
        {stage === "done" && (
          <section className="card stack">
            <h1>{t(lang, "ui.done_heading")}</h1>
            <p>{t(lang, "ui.done_body")}</p>
            <span className="pill ok">
              Sent to physician &middot; {Math.round(completeness)}% complete
            </span>
            <button
              className="btn secondary"
              onClick={async () => {
                await clearAll();
                setStage("language");
                setToken(null);
                setSessionToken(null);
                setQuestion(null);
                setResult(null);
                setCompleteness(0);
              }}
            >
              Next patient
            </button>
            <p className="small muted">
              Session data is cleared from this device before the next patient
              begins.
            </p>
          </section>
        )}
      </main>
    </div>
  );
}

/** Consent is explained in audio because we cannot assume the patient reads. */
function ConsentPanel({
  lang,
  busy,
  onSpeak,
  onAccept,
  onDecline,
}: {
  lang: Lang;
  busy: boolean;
  onSpeak: (text: string) => void;
  onAccept: (scopes: Scopes) => void;
  onDecline: () => void;
}) {
  const [scopes, setScopes] = useState<Scopes>({
    scope_interview: true,
    scope_documents: true,
    scope_abdm_share: false,
    scope_audio_retention: false,
  });

  const items: { key: keyof Scopes; label: string }[] = [
    { key: "scope_interview", label: t(lang, "ui.consent_interview") },
    { key: "scope_documents", label: t(lang, "ui.consent_documents") },
    { key: "scope_abdm_share", label: t(lang, "ui.consent_abdm") },
    { key: "scope_audio_retention", label: t(lang, "ui.consent_audio") },
  ];

  useEffect(() => {
    onSpeak(
      `${t(lang, "ui.consent_heading")}. ` +
        items.map((i) => i.label).join(". "),
    );
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section className="card stack">
      <h1>{t(lang, "ui.consent_heading")}</h1>
      <p className="muted small">{t(lang, "ui.consent_audio_note")}</p>

      <div className="stack">
        {items.map((item) => (
          <button
            key={item.key}
            className="checkbox"
            role="checkbox"
            aria-checked={scopes[item.key]}
            onClick={() =>
              setScopes((s) => ({ ...s, [item.key]: !s[item.key] }))
            }
          >
            <span className="box" aria-hidden>
              {scopes[item.key] ? "\u2714" : ""}
            </span>
            <span>{item.label}</span>
          </button>
        ))}
      </div>

      <div className="row">
        <button
          className="btn"
          disabled={busy || !scopes.scope_interview}
          onClick={() => onAccept(scopes)}
        >
          {t(lang, "ui.consent_accept")}
        </button>
        <button className="btn ghost" disabled={busy} onClick={onDecline}>
          {t(lang, "ui.consent_decline")}
        </button>
      </div>
      <p className="small muted">{t(lang, "ui.consent_decline_note")}</p>
    </section>
  );
}
