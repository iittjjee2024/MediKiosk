"use client";

import { useEffect, useState } from "react";

import * as api from "@/lib/api";

export type Staff = {
  token: string;
  user_id: string;
  full_name: string;
  role: string;
  department: string | null;
};

const KEY = "medikiosk.staff";

export function useStaff() {
  const [staff, setStaff] = useState<Staff | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const raw = sessionStorage.getItem(KEY);
    if (raw) {
      try {
        setStaff(JSON.parse(raw) as Staff);
      } catch {
        sessionStorage.removeItem(KEY);
      }
    }
    setReady(true);
  }, []);

  const signIn = (s: Staff) => {
    // sessionStorage, not localStorage: a clinical token must not survive the
    // browser session on a shared workstation
    sessionStorage.setItem(KEY, JSON.stringify(s));
    setStaff(s);
  };

  const signOut = () => {
    sessionStorage.removeItem(KEY);
    setStaff(null);
  };

  return { staff, ready, signIn, signOut };
}

export function LoginCard({
  onSignedIn,
  hint,
}: {
  onSignedIn: (s: Staff) => void;
  hint?: string;
}) {
  const [username, setUsername] = useState("dr.rao");
  const [password, setPassword] = useState("medikiosk-demo");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await api.login(username, password));
    } catch (err) {
      setError(err instanceof api.ApiError ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card stack" onSubmit={submit} style={{ maxWidth: 460 }}>
      <h2>Staff sign-in</h2>
      {hint && <p className="small muted">{hint}</p>}
      <div className="field">
        <label htmlFor="u">Username</label>
        <input
          id="u"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
        />
      </div>
      <div className="field">
        <label htmlFor="p">Password</label>
        <input
          id="p"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
      </div>
      {error && <div className="banner critical small">{error}</div>}
      <button className="btn" disabled={busy}>
        {busy ? "Signing in\u2026" : "Sign in"}
      </button>
    </form>
  );
}
