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

function toError(status: number, statusText: string, body: any, path: string): ApiError {
  const e = body?.error ?? {};
  if (status === 401 && path !== "/auth/login") {
    auth.set(null);
    window.dispatchEvent(new Event("rag:logout"));
  }
  const fallback = status === 413 ? "File is too large for the server." : status >= 500 ? "Server error." : statusText;
  return new ApiError(status, e.code ?? "HTTP_ERROR", e.message ?? (fallback || "Request failed."), e.request_id, e.details);
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (auth.token) headers.set("Authorization", `Bearer ${auth.token}`);
  if (init.body && !(init.body instanceof FormData) && !(init.body instanceof URLSearchParams))
    headers.set("Content-Type", "application/json");
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Could not reach the server. Check your connection and try again.");
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) throw toError(res.status, res.statusText, body, path);
  return body as T;
}

// Upload one file with real byte-level progress (fetch cannot report upload progress).
// onProgress(fraction) runs while bytes are sent; once they are all sent the server is processing.
export function uploadFile(file: File, onProgress: (fraction: number) => void): { promise: Promise<Doc>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<Doc>((resolve, reject) => {
    xhr.open("POST", `${API_URL}/documents`);
    if (auth.token) xhr.setRequestHeader("Authorization", `Bearer ${auth.token}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.upload.onload = () => onProgress(1);
    xhr.onload = () => {
      let body: any = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* non-JSON error page (e.g. proxy) */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as Doc);
      else reject(toError(xhr.status, xhr.statusText, body, "/documents"));
    };
    xhr.onerror = () => reject(new ApiError(0, "NETWORK_ERROR", "Upload failed: could not reach the server."));
    xhr.ontimeout = () => reject(new ApiError(0, "TIMEOUT", "Upload timed out."));
    xhr.onabort = () => reject(new ApiError(0, "ABORTED", "Upload cancelled."));
    const body = new FormData();
    body.append("file", file);
    xhr.send(body);
  });
  return { promise, abort: () => xhr.abort() };
}

// Small persisted UI state (survives page navigation and browser refresh). Storage can be unavailable.
export const saved = {
  get<T>(key: string, fallback: T): T {
    try {
      const raw = localStorage.getItem(`rag_${key}`);
      return raw === null ? fallback : (JSON.parse(raw) as T);
    } catch {
      return fallback;
    }
  },
  set(key: string, value: unknown) {
    try {
      if (value === null || value === undefined) localStorage.removeItem(`rag_${key}`);
      else localStorage.setItem(`rag_${key}`, JSON.stringify(value));
    } catch {
      /* storage unavailable: state lasts for this page only */
    }
  },
};

// ---------- types mirroring app/schemas.py ----------
export interface User { id: string; email: string; created_at: string }
export interface Doc {
  id: string; filename: string; file_type: string; size_bytes: number; page_count: number | null;
  chunk_count: number; flagged_chunks: number; empty_pages: number[]; status: string; created_at: string;
  can_reprocess: boolean; last_error: string | null;
}
export interface ContentBlock {
  position: number; section: string | null; page_start: number | null; page_end: number | null; kind: string; text: string;
}
export interface DocumentContent { document: Doc; blocks: ContentBlock[]; total_blocks: number; total_tokens: number }
export interface ReloadOut { document: Doc; mode: "reextracted" | "reindexed"; previous_chunk_count: number }
export interface UploadLimits { max_upload_mb: number; max_pages: number; extensions: string[] }
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
  message_id: string; citations: Citation[]; retrieved_count: number; kept_count: number; context_mode?: string;
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
export interface Trace {
  request_id: string; created_at: string; method: string; route: string; status_code: number; latency_ms: number;
  steps_ms: Record<string, number>; tokens_in: number; tokens_out: number; cost_usd: number;
  refused: boolean | null; error_type: string | null;
}
