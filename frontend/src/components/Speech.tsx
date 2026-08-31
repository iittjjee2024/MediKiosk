"use client";

/**
 * Speech helpers.
 *
 * Voice is the primary modality: the target patient may not read at all. But
 * touch is a fully equivalent path, not a fallback, so a hall too noisy for
 * reliable recognition degrades interaction quality rather than the
 * interview's completeness.
 *
 * Capture is push-to-talk. The microphone is live only while the patient holds
 * the button, which excludes most ambient waiting-hall noise by construction
 * rather than by filtering it afterwards.
 */

import { useCallback, useEffect, useRef, useState } from "react";

type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((e: any) => void) | null;
  onerror: ((e: any) => void) | null;
  onend: (() => void) | null;
};

function recognitionCtor(): (new () => SpeechRecognitionLike) | null {
  if (typeof window === "undefined") return null;
  const w = window as any;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/** Speaks a prompt aloud so the flow is fully navigable without reading. */
export function useSpeak(locale: string) {
  const [speaking, setSpeaking] = useState(false);
  const supported =
    typeof window !== "undefined" && "speechSynthesis" in window;

  const speak = useCallback(
    (text: string) => {
      if (!supported || !text) return;
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = locale;
      u.rate = 0.92; // slightly slow: many users are elderly
      u.onstart = () => setSpeaking(true);
      u.onend = () => setSpeaking(false);
      u.onerror = () => setSpeaking(false);
      window.speechSynthesis.speak(u);
    },
    [locale, supported],
  );

  const stop = useCallback(() => {
    if (!supported) return;
    window.speechSynthesis.cancel();
    setSpeaking(false);
  }, [supported]);

  useEffect(() => () => stop(), [stop]);
  return { speak, stop, speaking, supported };
}

export type Heard = {
  transcript: string;
  confidence: number;
};

/** Push-to-talk recognition. Returns a confidence the caller must respect. */
export function useListen(locale: string) {
  const ref = useRef<SpeechRecognitionLike | null>(null);
  const [listening, setListening] = useState(false);
  const [heard, setHeard] = useState<Heard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const supported = recognitionCtor() !== null;

  const start = useCallback(() => {
    const Ctor = recognitionCtor();
    if (!Ctor) {
      setError("unsupported");
      return;
    }
    setHeard(null);
    setError(null);
    const rec = new Ctor();
    rec.lang = locale;
    rec.continuous = false;
    rec.interimResults = false;
    rec.maxAlternatives = 3;

    rec.onresult = (e: any) => {
      const best = e.results?.[0]?.[0];
      if (!best) return;
      setHeard({
        transcript: String(best.transcript ?? "").trim(),
        // Some engines omit confidence. Treating an absent score as a low one
        // is the safe default: it routes the answer to confirm-back instead of
        // silently admitting an unverified value as a clinical fact.
        confidence:
          typeof best.confidence === "number" && best.confidence > 0
            ? best.confidence
            : 0.4,
      });
    };
    rec.onerror = (e: any) => {
      setError(String(e?.error ?? "error"));
      setListening(false);
    };
    rec.onend = () => setListening(false);

    ref.current = rec;
    try {
      rec.start();
      setListening(true);
    } catch {
      setError("start_failed");
    }
  }, [locale]);

  const stop = useCallback(() => {
    ref.current?.stop();
    setListening(false);
  }, []);

  useEffect(() => () => ref.current?.abort(), []);
  return { start, stop, listening, heard, error, supported, reset: () => setHeard(null) };
}
