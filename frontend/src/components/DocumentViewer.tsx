import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type ContentBlock, type Doc, type DocumentContent, type ReloadOut } from "../api";

const PAGE = 100;

export function StatusBadge({ doc }: { doc: Doc }) {
  const tone =
    doc.status === "ready" ? "bg-emerald-50 text-emerald-700" : doc.status === "failed" ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-700";
  return (
    <span title={doc.last_error ?? undefined} className={`rounded-full px-2 py-0.5 text-xs ${tone}`}>
      {doc.status}
    </span>
  );
}

export function errorText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return e.status === 429 ? `${e.message}` : `${e.message}${e.requestId ? ` (${e.requestId})` : ""}`;
  return fallback;
}

/** Re-process a stored document without uploading it again. */
export async function reloadDocument(doc: Doc): Promise<{ doc: Doc; note: string }> {
  const r = await api<ReloadOut>(`/documents/${doc.id}/reload`, { method: "POST" });
  const how = r.mode === "reextracted" ? "Re-read from the stored file" : "Re-indexed from the stored text";
  return { doc: r.document, note: `${how} · ${r.document.chunk_count} chunks` };
}

function Block({ b }: { b: ContentBlock }) {
  return (
    <div className="border-b border-stone-100 py-2 last:border-0">
      <div className="mb-1 flex flex-wrap gap-2 text-[11px] text-stone-400" dir="auto">
        {b.section && <span>{b.section}</span>}
        {b.page_start && <span>{b.page_start === b.page_end ? `p. ${b.page_start}` : `pp. ${b.page_start}–${b.page_end}`}</span>}
        {b.kind === "table" && <span className="rounded bg-sky-50 px-1.5 text-sky-700">table</span>}
      </div>
      {b.kind === "table" ? (
        <pre className="overflow-x-auto whitespace-pre rounded bg-stone-50 p-2 font-mono text-xs text-stone-700" dir="auto">{b.text}</pre>
      ) : (
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-stone-700" dir="auto">{b.text}</p>
      )}
    </div>
  );
}

/** Modal showing the extracted text exactly as it is used for answers, with a reload button. */
export default function DocumentViewer({ doc, onClose, onChanged }: { doc: Doc; onClose: () => void; onChanged?: (d: Doc) => void }) {
  const [data, setData] = useState<DocumentContent | null>(null);
  const [blocks, setBlocks] = useState<ContentBlock[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const load = useCallback(async (offset: number) => {
    setError("");
    try {
      const r = await api<DocumentContent>(`/documents/${doc.id}/content?offset=${offset}&limit=${PAGE}`);
      setData(r);
      setBlocks((prev) => (offset === 0 ? r.blocks : [...prev, ...r.blocks]));
    } catch (e) {
      setError(errorText(e, "Could not load the document text."));
    }
  }, [doc.id]);

  useEffect(() => { load(0); }, [load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function reload() {
    setBusy(true);
    setNote("");
    setError("");
    try {
      const r = await reloadDocument(doc);
      setNote(r.note);
      onChanged?.(r.doc);
      await load(0);
    } catch (e) {
      setError(errorText(e, "Reload failed."));
    } finally {
      setBusy(false);
    }
  }

  const d = data?.document ?? doc;
  return (
    <div className="fixed inset-0 z-20 flex items-start justify-center bg-stone-900/40 p-4 pt-16" onClick={onClose}>
      <div className="card flex max-h-[80vh] w-full max-w-3xl flex-col" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={`Text of ${d.filename}`}>
        <div className="flex flex-wrap items-center gap-3 border-b border-stone-100 px-4 py-3">
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium text-stone-800" dir="auto">{d.filename}</div>
            <div className="text-xs text-stone-500">
              {d.file_type.toUpperCase()} · {d.page_count ? `${d.page_count} pages · ` : ""}{d.chunk_count} chunks
              {data ? ` · ~${data.total_tokens.toLocaleString()} tokens` : ""}
            </div>
          </div>
          <StatusBadge doc={d} />
          <button className="btn-ghost" disabled={busy} onClick={reload} title="Re-process the stored document without uploading it again">
            {busy ? "Reloading…" : "Reload"}
          </button>
          <button className="btn-ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {note && <p className="bg-emerald-50 px-4 py-2 text-sm text-emerald-800">{note}</p>}
        {error && <p className="bg-red-50 px-4 py-2 text-sm text-red-700">{error}</p>}
        <div className="flex-1 overflow-y-auto px-4">
          {!data && !error && <p className="py-6 text-sm text-stone-400">Loading extracted text…</p>}
          {data && blocks.length === 0 && <p className="py-6 text-sm text-stone-400">No text was extracted from this document.</p>}
          {blocks.map((b) => <Block key={b.position} b={b} />)}
          {data && blocks.length < data.total_blocks && (
            <button className="btn-ghost my-2 w-full" onClick={() => load(blocks.length)}>
              Show more ({data.total_blocks - blocks.length} left)
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
