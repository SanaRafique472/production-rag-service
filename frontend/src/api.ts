// Small typed client for the FastAPI backend. Every error comes back as
// { error: { code, message, request_id, details } } and is thrown as ApiError.

export const API_URL: string = import.meta.env.VITE_API_URL ?? "/api";
const TOKEN_KEY = "rag_token";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export const auth = {
  get token(): string | null {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set(token: string | null) {
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* storage unavailable: stay logged in for this tab only */
    }
  },
};

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (auth.token) headers.set("Authorization", `Bearer ${auth.token}`);
  if (init.body && !(init.body instanceof FormData) && !(init.body instanceof URLSearchParams))
    headers.set("Content-Type", "application/json");
  const res = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const e = body?.error ?? {};
    if (res.status === 401 && path !== "/auth/login") {
      auth.set(null);
      window.dispatchEvent(new Event("rag:logout"));
    }
    throw new ApiError(res.status, e.code ?? "HTTP_ERROR", e.message ?? res.statusText, e.request_id, e.details);
  }
  return body as T;
}

// ---------- types mirroring app/schemas.py ----------
export interface User { id: string; email: string; created_at: string }
export interface Doc {
  id: string; filename: string; file_type: string; size_bytes: number; page_count: number | null;
  chunk_count: number; flagged_chunks: number; empty_pages: number[]; status: string; created_at: string;
}
export interface Citation {
  n: number; chunk_id: string; document_id: string; filename: string;
  page_start: number | null; page_end: number | null; section: string | null; cited_text: string;
}
export interface Chunk {
  chunk_id: string; document_id: string; filename: string; position: number;
  page_start: number | null; page_end: number | null; section: string | null; text: string; kind: string;
  similarity: number | null; dense_rank: number | null; bm25_rank: number | null; rrf_score: number;
  rerank_score: number | null; kept: boolean; cited: boolean; suspicious: boolean;
}
export interface AskResponse {
  answer: string; refused: boolean; refusal_reason: string | null; language: string; session_id: string;
  message_id: string; citations: Citation[]; retrieved_count: number; kept_count: number;
  retrieved_chunks: Chunk[]; retrieval_config: string;
  usage: { tokens_in: number; tokens_out: number; cost_usd: number };
  latency_ms: number; timings_ms: Record<string, number>; request_id: string;
}
export interface Message {
  id: string; role: "user" | "assistant"; content: string; refused: boolean; refusal_reason: string | null;
  citations: Citation[]; retrieved_chunks: Chunk[]; latency_ms: number | null; created_at: string;
}
export interface Session { id: string; title: string; created_at: string; messages: Message[] }
export interface Metrics {
  window_hours: number; requests: number; error_rate: number; rate_limited: number; questions: number;
  refusal_rate: number | null; latency_p50_ms: number | null; latency_p95_ms: number | null;
  step_p50_ms: Record<string, number | null>; avg_tokens_in: number | null; avg_tokens_out: number | null;
  avg_cost_usd: number | null; total_cost_usd: number;
  by_route: Record<string, { count: number; p50_ms: number; p95_ms: number; errors: number }>;
  timeline: { hour: string; questions: number; p50_ms: number | null; p95_ms: number | null; cost_usd: number }[];
}
export interface EvalRunSummary {
  run_id: string; created_at: string; mode: string; configs: string[];
  summary: Record<string, Record<string, unknown>>;
}
export interface Health { status: string; mode: string; checks: Record<string, string> }
