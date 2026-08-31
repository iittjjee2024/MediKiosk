
import { enqueue, newKey } from "./offline";

export type Question = {
  field_code: string;
  section: string;
  prompt_key: string;
  answer_type: "single" | "multi" | "scale" | "duration" | "numeric" | "free_text";
  options: string[];
  clinical_concept: string | null;
};

export type Completeness = {
  score: number;
  applicable_required: number;
  answered_required: number;
  unanswered: string[];
  skipped: { field_code: string; section: string; reason: string }[];
  explanation: string;
};

export type Alert = {
  id: string;
  rule_code: string;
  rule_version: number;
  severity: "critical" | "high" | "moderate" | "low";
  message_key: string | null;
  status: string;
  sla_deadline: string | null;
};

export type AnswerResult = {
  field_code: string;
  confidence_band: "high" | "medium" | "low" | "unreadable";
  fact_admitted: boolean;
  disposition: string;
  confirm_back_required: boolean;
  admitted_fact_ids: string[];
  verification_item_ids: string[];
  superseded_previous: boolean;
  red_flag_fired: boolean;
  alerts: Alert[];
  completeness: Completeness;
  next_question: Question | null;
};

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { token?: string; idempotent?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string>) ?? {}),
  };
  if (init.token) headers.Authorization = `Bearer ${init.token}`;
  if (init.idempotent !== false && init.method && init.method !== "GET") {
    headers["Idempotency-Key"] = newKey();
  }

  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {

    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function resolveIdentity(body: {
  display_name?: string;
  abha_id?: string;
  hospital_local_id?: string;
  gender?: string;
  year_of_birth?: number;
  care_system?: "allopathic" | "ayush";
  language: string;
  device_id?: string;
  channel?: string;
}) {
  return request<{
    token: string;
    patient_id: string;
    encounter_id: string;
    identity_source: string;
  }>("/api/v1/identity/resolve", { method: "POST", body: JSON.stringify(body) });
}

export function grantConsent(
  token: string,
  body: {
    scope_interview: boolean;
    scope_documents: boolean;
    scope_abdm_share: boolean;
    scope_audio_retention: boolean;
    language: string;
    explained_via_audio: boolean;
  },
) {
  return request<{
    consent_id: string;
    granted: boolean;
    scopes: Record<string, boolean>;
    session_id: string | null;
    message: string;
  }>("/api/v1/consent", { method: "POST", token, body: JSON.stringify(body) });
}

export function bindSessionToken(token: string, sessionId: string) {
  return request<{ token: string }>(`/api/v1/sessions/${sessionId}/token`, {
    method: "POST",
    token,
    idempotent: false,
  });
}

export function nextQuestion(token: string) {
  return request<Question | null>("/api/v1/interview/next-question", { token });
}

export type AnswerBody = {
  field_code: string;
  value?: string;
  selected_options?: string[];
  input_mode: "touch" | "voice";
  raw_transcript?: string;
  asr_confidence?: number;
  nlu_confidence?: number;
  skipped_reason?: "not_applicable" | "declined";
};

export function submitAnswer(token: string, body: AnswerBody) {
  return request<AnswerResult>("/api/v1/interview/answers", {
    method: "POST",
    token,
    body: JSON.stringify(body),
  });
}


export function queueAnswer(token: string, body: AnswerBody) {
  return enqueue({
    idempotencyKey: newKey(),
    url: "/api/v1/interview/answers",
    method: "POST",
    body,
    token,
    createdAt: Date.now(),
  });
}

export function interviewState(token: string) {
  return request<{
    session_id: string;
    status: string;
    language: string;
    completeness: Completeness;
    progress: Record<string, { answered: number; applicable: number }>;
    next_question: Question | null;
    answered: number;
    facts: number;
  }>("/api/v1/interview/state", { token });
}

export function finalise(token: string) {
  return request<{
    session_id: string;
    status: string;
    completeness: Completeness;
    summary_id: string;
    grounding_pass_rate: number;
  }>("/api/v1/interview/finalise", { method: "POST", token });
}

export function prakriti(token: string) {
  return request<{
    distribution: Record<string, number>;
    raw_totals: Record<string, number>;
    indicated_dominant: string | null;
    contributions: {
      field_code: string;
      value: string;
      weights: Record<string, number>;
    }[];
    status: string;
    disclaimer: string;
  }>("/api/v1/interview/ayush/prakriti", { token });
}

export function login(username: string, password: string) {
  return request<{
    token: string;
    user_id: string;
    full_name: string;
    role: string;
    department: string | null;
  }>("/api/v1/auth/login", {
    method: "POST",
    idempotent: false,
    body: JSON.stringify({ username, password }),
  });
}

export type WorklistRow = {
  session_id: string;
  status: string;
  completeness: number;
  care_system: string;
  department: string;
  patient: {
    id: string;
    name: string;
    gender: string | null;
    year_of_birth: number | null;
    abha_linked: boolean;
  };
  alerts: Alert[];
  summary_id: string | null;
  grounding_pass_rate: number | null;
};

export function worklist(token: string) {
  return request<{ count: number; sessions: WorklistRow[] }>(
    "/api/v1/physician/worklist",
    { token },
  );
}

export type Provenance = {
  source_type: string;
  document_id: string | null;
  page_number: number | null;
  region_bbox: unknown;
  extraction_method: string;
  model_name: string | null;
  model_version: string | null;
  confidence: number;
  captured_at: string;
  device_id: string | null;
};

export type Citation = {
  fact_id: string;
  category: string;
  label: string | null;
  value: string | null;
  confidence: number;
  verification_status: string;
  physician_status: string;
  is_conflicting: boolean;
  provenance: Provenance | null;
};

export type CitedSentence = {
  sentence_id: string;
  section: string;
  order: number;
  text: string;
  citations: Citation[];
};

export type SummaryView = {
  cited_summary: {
    summary_id: string;
    version: number;
    status: string;
    grounding_pass_rate: number;
    sentences: CitedSentence[];
  };
  body: {
    sections: Record<string, { text: string; fact_ids: string[] }[]>;
    labels: Record<string, string>;
    order: string[];
  };
  pending_documents: number;
  interaction_check_performed: boolean;
  conflicts: {
    fact_id: string;
    group: string | null;
    label: string | null;
    value: string | null;
    source_type: string;
  }[];
  needs_human_verification: {
    id: string;
    field_code: string | null;
    candidate_text: string | null;
    confidence: number;
    reason: string;
  }[];
};

export function sessionSummary(token: string, sessionId: string) {
  return request<SummaryView>(
    `/api/v1/physician/sessions/${sessionId}/summary`,
    { token },
  );
}

export function editFact(
  token: string,
  summaryId: string,
  factId: string,
  newValue: string,
) {
  return request<{
    fact_id: string;
    value: string;
    physician_status: string;
    summary_status: string;
  }>(`/api/v1/physician/summaries/${summaryId}/facts`, {
    method: "PATCH",
    token,
    body: JSON.stringify({ fact_id: factId, new_value: newValue }),
  });
}

export function rejectFact(
  token: string,
  summaryId: string,
  factId: string,
  note?: string,
) {
  return request<{ fact_id: string; physician_status: string }>(
    `/api/v1/physician/summaries/${summaryId}/facts/reject`,
    {
      method: "POST",
      token,
      body: JSON.stringify({ fact_id: factId, note }),
    },
  );
}

export function approveSummary(
  token: string,
  summaryId: string,
  acknowledgeConflicts: boolean,
) {
  return request<{
    final_clinical_record_id: string;
    summary_version: number;
    content_hash: string;
    facts: { accepted: number; edited: number; rejected: number };
    integration: Record<string, string>;
  }>(`/api/v1/physician/summaries/${summaryId}/approve`, {
    method: "POST",
    token,
    body: JSON.stringify({
      unresolved_conflicts_acknowledged: acknowledgeConflicts,
    }),
  });
}

export function fhirBundle(token: string, sessionId: string) {
  return request<Record<string, unknown>>(
    `/api/v1/physician/sessions/${sessionId}/fhir`,
    { token },
  );
}

export type TriageAlert = Alert & {
  triggering_facts: { refs: string[]; errored: boolean };
  sla_breached: boolean;
  session_id: string;
  patient: { id: string; name: string };
};

export function triageAlerts(token: string) {
  return request<{ count: number; alerts: TriageAlert[] }>(
    "/api/v1/triage/alerts",
    { token },
  );
}

export function acknowledgeAlert(token: string, alertId: string, note?: string) {
  return request<{ alert_id: string; status: string; acknowledged_at: string }>(
    `/api/v1/triage/alerts/${alertId}/acknowledge`,
    { method: "POST", token, body: JSON.stringify({ note }) },
  );
}

export function dashboard(token: string) {
  return request<{
    sessions: Record<string, number>;
    facts: Record<string, number>;
    quality: Record<string, number | null>;
    triage: Record<string, number | null>;
  }>("/api/v1/analytics/dashboard", { token });
}
