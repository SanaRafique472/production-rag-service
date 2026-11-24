import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError, saved, type AskResponse, type Citation, type Doc, type Session } from "../api";
import DocumentViewer, { errorText, reloadDocument, StatusBadge } from "../components/DocumentViewer";
import EvidencePanel, { where } from "../components/EvidencePanel";

type Turn = { q: string; res?: AskResponse; error?: string };

const REASONS: Record<string, string> = {
  no_documents_uploaded: "no documents",
  no_relevant_chunks: "not in the document",
  not_in_documents: "not in the document",
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
  const [params, setParams] = useSearchParams();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  // Session and document scope survive page navigation and browser refresh.
  const [sessionId, setSessionId] = useState<string | null>(() => saved.get<string | null>("chat_session", null));
  const [selected, setSelected] = useState<AskResponse | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [docsError, setDocsError] = useState("");
  const [scope, setScope] = useState<string[]>(() => {
    const fromLink = params.get("doc");
    return fromLink ? [fromLink] : saved.get<string[]>("chat_scope", []);
  });
  const [sessions, setSessions] = useState<Session[]>([]);
  const [viewing, setViewing] = useState<Doc | null>(null);
  const [reloading, setReloading] = useState<Record<string, string>>({});
  const bottom = useRef<HTMLDivElement>(null);

  const loadDocs = useCallback(() => {
    api<{ items: Doc[] }>("/documents?limit=200")
      .then((r) => {
        setDocs(r.items);
        setDocsError("");
        setScope((s) => s.filter((id) => r.items.some((d) => d.id === id))); // forget deleted documents
      })
      .catch((e) => setDocsError(errorText(e, "Could not load your documents.")));
  }, []);

  useEffect(() => {
    loadDocs();
    api<{ sessions: Session[] }>("/chat/history?limit=20").then((r) => setSessions(r.sessions)).catch(() => {});
    const restore = saved.get<string | null>("chat_session", null);
    if (restore)
      api<{ sessions: Session[] }>(`/chat/history?session_id=${restore}&limit=1`)
        .then((r) => r.sessions[0] && openSession(r.sessions[0]))
        .catch(() => { saved.set("chat_session", null); setSessionId(null); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadDocs]);
  useEffect(() => saved.set("chat_session", sessionId), [sessionId]);
  useEffect(() => {
    saved.set("chat_scope", scope);
    if (params.get("doc")) setParams({}, { replace: true }); // the link's choice is now remembered
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  async function reload(doc: Doc) {
    setReloading((r) => ({ ...r, [doc.id]: "Reloading…" }));
    try {
      const r = await reloadDocument(doc);
      setReloading((x) => ({ ...x, [doc.id]: `✓ ${r.note}` }));
    } catch (e) {
      setReloading((x) => ({ ...x, [doc.id]: `✕ ${errorText(e, "Reload failed.")}` }));
    } finally {
      loadDocs();
      setTimeout(() => setReloading((x) => { const { [doc.id]: _, ...rest } = x; return rest; }), 6000);
    }
  }

  const docList = docs ?? [];
  const active = docList.filter((d) => scope.includes(d.id));
  const notReady = active.filter((d) => d.status !== "ready");
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
        ? `${err.message}${err.status ? ` (${err.code}${err.requestId ? ` · ${err.requestId}` : ""})` : ""}`
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
            {docs === null && !docsError && <p className="text-stone-400">Loading…</p>}
            {docsError && <p className="text-red-600">{docsError} <button className="underline" onClick={loadDocs}>Retry</button></p>}
            {docs?.length === 0 && <p className="text-stone-400">No documents. Upload one on the Documents page.</p>}
            {docList.map((d) => (
              <label key={d.id} className="flex items-center gap-2 text-stone-600" dir="auto" title={`${d.file_type.toUpperCase()} · ${d.status}`}>
                <input type="checkbox" checked={scope.includes(d.id)}
                  onChange={(e) => setScope((s) => (e.target.checked ? [...s, d.id] : s.filter((x) => x !== d.id)))} />
                <span className="truncate">{d.filename}</span>
                {d.status !== "ready" && <span className="text-xs text-amber-700">{d.status}</span>}
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
        {active.length > 0 && (
          <div className="card mb-4 space-y-2 px-4 py-3">
            {active.map((d) => (
              <div key={d.id} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="rounded bg-stone-100 px-1.5 text-xs uppercase text-stone-600">{d.file_type}</span>
                <span className="min-w-0 flex-1 truncate font-medium text-stone-800" dir="auto">{d.filename}</span>
                <span className="text-xs text-stone-500">{d.page_count ? `${d.page_count} pages · ` : ""}{d.chunk_count} chunks</span>
                <StatusBadge doc={d} />
                <button className="btn-ghost px-2 py-1" onClick={() => setViewing(d)}>View text</button>
                <button className="btn-ghost px-2 py-1" onClick={() => reload(d)} disabled={reloading[d.id] === "Reloading…"}>
                  {reloading[d.id] === "Reloading…" ? "Reloading…" : "Reload"}
                </button>
                <button className="btn-ghost px-2 py-1" aria-label={`Stop using ${d.filename}`} onClick={() => setScope((s) => s.filter((x) => x !== d.id))}>✕</button>
                {reloading[d.id] && reloading[d.id] !== "Reloading…" && (
                  <div className={`w-full text-xs ${reloading[d.id].startsWith("✕") ? "text-red-600" : "text-emerald-700"}`}>{reloading[d.id]}</div>
                )}
              </div>
            ))}
            {notReady.length > 0 && <p className="text-xs text-amber-700">A selected document is not ready. Reload it to rebuild its index.</p>}
          </div>
        )}
        {active.length === 0 && docList.length > 0 && (
          <p className="mb-3 text-xs text-stone-500">Asking across all {docList.length} documents. Select one on the left (or use “Ask” on the Documents page) to focus on it.</p>
        )}
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
                  {!t.res.refused && t.res.context_mode && (
                    <span className="mb-1 inline-block rounded bg-brand-50 px-1.5 text-xs text-brand-700"
                      title={t.res.context_mode === "full_document" ? "The whole document was given to the model" : "The most relevant passages were given to the model"}>
                      {t.res.context_mode === "full_document" ? "read whole document" : "top passages"}
                    </span>
                  )}
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
          <input className="input" dir="auto" maxLength={1000}
            placeholder={active.length === 1 ? `Ask anything about ${active[0].filename}` : "e.g. How many paid sick days do I get?"}
            value={question} onChange={(e) => setQuestion(e.target.value)} />
          <button className="btn-primary" disabled={busy || !question.trim()}>{busy ? "…" : "Ask"}</button>
        </form>
      </section>

      <aside><EvidencePanel res={selected} focus={focus} /></aside>
      {viewing && <DocumentViewer doc={viewing} onClose={() => setViewing(null)} onChanged={() => loadDocs()} />}
    </div>
  );
}
