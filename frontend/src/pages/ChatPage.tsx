import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api, ApiError, type AskResponse, type Citation, type Doc, type Session } from "../api";
import EvidencePanel, { where } from "../components/EvidencePanel";

type Turn = { q: string; res?: AskResponse; error?: string };

const REASONS: Record<string, string> = {
  no_documents_uploaded: "no documents",
  no_relevant_chunks: "nothing relevant found",
  not_in_documents: "answer not in documents",
  off_topic: "not a document question",
  output_blocked: "blocked by output check",
};

// Turn "[1]" markers into buttons that highlight the cited chunk in the evidence panel.
function withCitations(text: string, citations: Citation[], onCite: (c: Citation) => void): ReactNode[] {
  return text.split(/(\[\d+\])/g).map((part, i) => {
    const m = part.match(/^\[(\d+)\]$/);
    const cit = m && citations.find((c) => c.n === Number(m[1]));
    return cit ? (
      <button key={i} onClick={() => onCite(cit)} title={where(cit)}
        className="mx-0.5 rounded bg-brand-100 px-1 align-super text-[11px] font-semibold text-brand-900 hover:bg-brand-600 hover:text-white">
        {cit.n}
      </button>
    ) : <span key={i}>{part}</span>;
  });
}

export default function ChatPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [selected, setSelected] = useState<AskResponse | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [scope, setScope] = useState<string[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<{ items: Doc[] }>("/documents?limit=200").then((r) => setDocs(r.items)).catch(() => {});
    api<{ sessions: Session[] }>("/chat/history?limit=20").then((r) => setSessions(r.sessions)).catch(() => {});
  }, []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" }); // block body: newer browsers return a Promise here
  }, [turns]);

  async function ask(e: FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || busy) return;
    setQuestion("");
    setBusy(true);
    setTurns((t) => [...t, { q }]);
    try {
      const res = await api<AskResponse>("/chat/ask", {
        method: "POST",
        body: JSON.stringify({ question: q, session_id: sessionId, document_ids: scope.length ? scope : null }),
      });
      setSessionId(res.session_id);
      setSelected(res);
      setFocus(res.citations[0]?.chunk_id ?? null);
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, res } : x)));
    } catch (err) {
      const msg = err instanceof ApiError
        ? `${err.message} (${err.code}${err.requestId ? ` · ${err.requestId}` : ""})`
        : "Could not reach the server.";
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, error: msg } : x)));
    } finally {
      setBusy(false);
    }
  }

  function openSession(s: Session) {
    setSessionId(s.id);
    setSelected(null);
    const out: Turn[] = [];
    for (let i = 0; i < s.messages.length; i += 2) {
      const [u, a] = [s.messages[i], s.messages[i + 1]];
      if (!u) continue;
      out.push({ q: u.content, res: a && ({
        answer: a.content, refused: a.refused, refusal_reason: a.refusal_reason, citations: a.citations,
        retrieved_chunks: a.retrieved_chunks, retrieved_count: a.retrieved_chunks.length, kept_count: a.retrieved_chunks.length,
        latency_ms: a.latency_ms ?? 0, usage: { tokens_in: 0, tokens_out: 0, cost_usd: 0 }, timings_ms: {},
        retrieval_config: "—", language: "", session_id: s.id, message_id: a.id, request_id: "",
      } as AskResponse) });
    }
    setTurns(out);
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)_420px]">
      <aside className="hidden space-y-4 lg:block">
        <button className="btn-primary w-full" onClick={() => { setTurns([]); setSessionId(null); setSelected(null); }}>New chat</button>
        <div>
          <div className="label mb-1">Search in</div>
          <div className="max-h-48 space-y-1 overflow-y-auto text-sm">
            {docs.length === 0 && <p className="text-stone-400">No documents.</p>}
            {docs.map((d) => (
              <label key={d.id} className="flex items-center gap-2 text-stone-600" dir="auto">
                <input type="checkbox" checked={scope.includes(d.id)}
                  onChange={(e) => setScope((s) => (e.target.checked ? [...s, d.id] : s.filter((x) => x !== d.id)))} />
                <span className="truncate">{d.filename}</span>
              </label>
            ))}
          </div>
          <p className="mt-1 text-xs text-stone-400">{scope.length ? `${scope.length} selected` : "All documents"}</p>
        </div>
        <div>
          <div className="label mb-1">History</div>
          <ul className="space-y-1 text-sm">
            {sessions.map((s) => (
              <li key={s.id}><button className={`w-full truncate rounded px-2 py-1 text-left hover:bg-stone-100 ${s.id === sessionId ? "bg-stone-100" : ""}`}
                onClick={() => openSession(s)} dir="auto">{s.title}</button></li>
            ))}
          </ul>
        </div>
      </aside>

      <section className="flex min-h-[70vh] flex-col">
        <div className="flex-1 space-y-5">
          {turns.length === 0 && (
            <div className="card p-8 text-center text-stone-500">
              <p className="font-medium text-stone-700">Ask a question about your documents</p>
              <p className="mt-1 text-sm">Answers cite the exact passages they come from. If the answer is not in your files, you will be told so. Urdu and Arabic questions work too.</p>
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} className="space-y-2">
              <div className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-brand-700 px-4 py-2 text-white" dir="auto">{t.q}</div>
              {t.error && <div className="w-fit rounded-2xl bg-red-50 px-4 py-2 text-sm text-red-700">{t.error}</div>}
              {!t.res && !t.error && <div className="w-fit rounded-2xl bg-white px-4 py-2 text-sm text-stone-400 shadow-sm">Searching your documents…</div>}
              {t.res && (
                <div onClick={() => setSelected(t.res!)}
                  className={`card max-w-[95%] cursor-pointer px-4 py-3 ${selected === t.res ? "ring-2 ring-brand-100" : ""} ${t.res.refused ? "bg-stone-50" : ""}`}>
                  {t.res.refused && <span className="mb-1 inline-block rounded bg-stone-200 px-1.5 text-xs text-stone-600">refused · {REASONS[t.res.refusal_reason ?? ""] ?? t.res.refusal_reason}</span>}
                  <p className="whitespace-pre-wrap leading-relaxed text-stone-800" dir="auto">
                    {withCitations(t.res.answer, t.res.citations, (c) => { setSelected(t.res!); setFocus(c.chunk_id); document.getElementById(`chunk-${c.chunk_id}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" }); })}
                  </p>
                  {t.res.citations.length > 0 && (
                    <ol className="mt-2 space-y-0.5 border-t border-stone-100 pt-2 text-xs text-stone-500">
                      {t.res.citations.map((c) => <li key={c.n} dir="auto">[{c.n}] {where(c)}{c.section && c.page_start ? ` — ${c.section}` : ""}</li>)}
                    </ol>
                  )}
                </div>
              )}
            </div>
          ))}
          <div ref={bottom} />
        </div>
        <form onSubmit={ask} className="sticky bottom-0 mt-4 flex gap-2 bg-stone-50 py-2">
          <input className="input" dir="auto" maxLength={1000} placeholder="e.g. How many paid sick days do I get?"
            value={question} onChange={(e) => setQuestion(e.target.value)} />
          <button className="btn-primary" disabled={busy || !question.trim()}>{busy ? "…" : "Ask"}</button>
        </form>
      </section>

      <aside><EvidencePanel res={selected} focus={focus} /></aside>
    </div>
  );
}
