"use client";

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
      u.rate = 0.92;
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
