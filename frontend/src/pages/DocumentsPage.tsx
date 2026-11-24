import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, saved, uploadFile, type Doc, type UploadLimits } from "../api";
import DocumentViewer, { errorText, reloadDocument, StatusBadge } from "../components/DocumentViewer";

const DEFAULT_LIMITS: UploadLimits = {
  max_upload_mb: 50, max_pages: 1000, extensions: [".pdf", ".docx", ".doc", ".rtf", ".pptx", ".txt", ".md", ".markdown"],
};
const fmtSize = (b: number) => (b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);
const extOf = (name: string) => (name.includes(".") ? name.slice(name.lastIndexOf(".")).toLowerCase() : "");

type Stage = "checking" | "uploading" | "processing" | "done" | "error";
type Upload = { key: string; name: string; type: string; size: number; stage: Stage; progress: number; message?: string; abort?: () => void };

export default function DocumentsPage() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [limits, setLimits] = useState<UploadLimits>(DEFAULT_LIMITS);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const [viewing, setViewing] = useState<Doc | null>(null);
  const [rowBusy, setRowBusy] = useState<Record<string, string>>({});
  const input = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const load = useCallback(() => {
    api<{ items: Doc[] }>("/documents?limit=200")
      .then((r) => { setDocs(r.items); setError(""); })
      .catch((e) => setError(errorText(e, "Could not load your documents.")));
  }, []);
  useEffect(load, [load]);
  useEffect(() => { api<UploadLimits>("/documents/limits").then(setLimits).catch(() => {}); }, []);

  const patch = (key: string, p: Partial<Upload>) => setUploads((u) => u.map((x) => (x.key === key ? { ...x, ...p } : x)));

  function validate(file: File): string | null {
    const ext = extOf(file.name);
    if (!limits.extensions.includes(ext)) return `Unsupported file type '${ext || "none"}'. Use PDF, DOCX, DOC, RTF, PPTX, TXT or MD.`;
    if (file.size === 0) return "The file is empty.";
    if (file.size > limits.max_upload_mb * 1024 * 1024) return `File is ${fmtSize(file.size)}; the limit is ${limits.max_upload_mb} MB.`;
    return null;
  }

  async function uploadFiles(files: FileList | File[]) {
    const list = Array.from(files);
    const entries: Upload[] = list.map((f, i) => ({
      key: `${Date.now()}-${i}-${f.name}`, name: f.name, type: extOf(f.name).slice(1).toUpperCase() || "?", size: f.size, stage: "checking", progress: 0,
    }));
    setUploads((u) => [...entries, ...u].slice(0, 20));
    for (const [i, file] of list.entries()) {
      const { key } = entries[i];
      const invalid = validate(file);
      if (invalid) { patch(key, { stage: "error", message: invalid }); continue; }
      const job = uploadFile(file, (f) => patch(key, f < 1 ? { stage: "uploading", progress: f } : { stage: "processing", progress: 1 }));
      patch(key, { stage: "uploading", abort: job.abort });
      try {
        const doc = await job.promise;
        const note = doc.flagged_chunks ? ` · ${doc.flagged_chunks} chunk(s) contain instructions aimed at AI and will be treated as data` : "";
        patch(key, { stage: "done", abort: undefined, type: doc.file_type.toUpperCase(), message: `Ready · ${doc.chunk_count} chunks indexed${note}` });
        load();
      } catch (e) {
        patch(key, { stage: "error", abort: undefined, message: errorText(e, "Upload failed.") });
      }
    }
    if (input.current) input.current.value = ""; // allow choosing the same file again
  }

  async function reload(doc: Doc) {
    setRowBusy((b) => ({ ...b, [doc.id]: "Reloading…" }));
    try {
      const r = await reloadDocument(doc);
      setRowBusy((b) => ({ ...b, [doc.id]: `✓ ${r.note}` }));
    } catch (e) {
      setRowBusy((b) => ({ ...b, [doc.id]: `✕ ${errorText(e, "Reload failed.")}` }));
    } finally {
      load();
      setTimeout(() => setRowBusy((b) => { const { [doc.id]: _, ...rest } = b; return rest; }), 6000);
    }
  }

  async function remove(doc: Doc) {
    if (!confirm(`Delete ${doc.filename}? Its chunks, stored file and vectors are removed and it can no longer be cited.`)) return;
    try {
      await api(`/documents/${doc.id}`, { method: "DELETE" });
      const scope = saved.get<string[]>("chat_scope", []);
      saved.set("chat_scope", scope.filter((id) => id !== doc.id));
      load();
    } catch (e) {
      setError(errorText(e, "Delete failed."));
    }
  }

  function ask(doc: Doc) {
    saved.set("chat_scope", [doc.id]);
    navigate(`/chat?doc=${doc.id}`);
  }

  const busy = uploads.some((u) => u.stage === "uploading" || u.stage === "processing" || u.stage === "checking");

  return (
    <div className="space-y-6">
      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); uploadFiles(e.dataTransfer.files); }}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        role="button" tabIndex={0}
        className={`card flex cursor-pointer flex-col items-center gap-2 border-2 border-dashed p-10 text-center transition ${drag ? "border-brand-600 bg-brand-50" : "border-stone-300"}`}>
        <p className="font-medium text-stone-700">{busy ? "Uploading… you can add more files" : "Drop files here or click to upload"}</p>
        <p className="text-sm text-stone-500">PDF, DOCX, DOC, RTF, PPTX, TXT or MD · up to {limits.max_upload_mb} MB · scanned PDFs without a text layer are rejected</p>
        <input ref={input} type="file" multiple accept={limits.extensions.join(",")} hidden onChange={(e) => e.target.files && uploadFiles(e.target.files)} />
      </div>

      {uploads.length > 0 && (
        <ul className="space-y-1">
          {uploads.map((u) => (
            <li key={u.key} className={`rounded-lg px-3 py-2 text-sm ${u.stage === "error" ? "bg-red-50 text-red-700" : u.stage === "done" ? "bg-emerald-50 text-emerald-800" : "bg-stone-100 text-stone-600"}`}>
              <div className="flex items-center gap-2">
                <span className="truncate font-medium" dir="auto">{u.name}</span>
                <span className="shrink-0 rounded bg-white/70 px-1.5 text-xs uppercase">{u.type}</span>
                <span className="shrink-0 text-xs opacity-70">{fmtSize(u.size)}</span>
                <span className="ml-auto shrink-0 text-xs">
                  {u.stage === "checking" && "Waiting…"}
                  {u.stage === "uploading" && `Uploading ${Math.round(u.progress * 100)}%`}
                  {u.stage === "processing" && "Processing: reading text and indexing…"}
                </span>
                {u.abort && u.stage === "uploading" && <button className="text-xs underline" onClick={() => u.abort?.()}>Cancel</button>}
                {(u.stage === "done" || u.stage === "error") && (
                  <button className="text-xs opacity-60 hover:opacity-100" aria-label="Dismiss" onClick={() => setUploads((x) => x.filter((y) => y.key !== u.key))}>✕</button>
                )}
              </div>
              {(u.stage === "uploading" || u.stage === "processing") && (
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white">
                  <div className={`h-full rounded-full bg-brand-600 transition-all ${u.stage === "processing" ? "w-full animate-pulse" : ""}`}
                    style={u.stage === "uploading" ? { width: `${Math.max(3, u.progress * 100)}%` } : undefined} />
                </div>
              )}
              {u.message && <div className="mt-0.5">{u.message}</div>}
            </li>
          ))}
        </ul>
      )}
      {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error} <button className="underline" onClick={load}>Retry</button></p>}

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 text-left text-stone-500">
            <tr>{["File", "Type", "Size", "Pages", "Chunks", "Status", "Uploaded", ""].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody>
            {docs.length === 0 && (
              <tr><td colSpan={8} className="px-4 py-10 text-center text-stone-400">No documents yet. Upload one to start asking questions.</td></tr>
            )}
            {docs.map((d) => (
              <tr key={d.id} className="border-t border-stone-100 align-top">
                <td className="px-4 py-2 font-medium text-stone-800" dir="auto">
                  <button className="text-left hover:text-brand-700 hover:underline" onClick={() => setViewing(d)} title="View extracted text">{d.filename}</button>
                  {d.flagged_chunks > 0 && <span title="Contains text that looks like instructions to an AI" className="ml-2 rounded bg-amber-100 px-1.5 text-xs text-amber-800">⚠ {d.flagged_chunks}</span>}
                  {d.empty_pages.length > 0 && <span title="Pages without extractable text" className="ml-2 text-xs text-stone-400">no text on p. {d.empty_pages.join(", ")}</span>}
                  {rowBusy[d.id] && <div className={`mt-0.5 text-xs font-normal ${rowBusy[d.id].startsWith("✕") ? "text-red-600" : "text-emerald-700"}`}>{rowBusy[d.id]}</div>}
                  {!rowBusy[d.id] && d.last_error && <div className="mt-0.5 text-xs font-normal text-red-600">Last reload failed: {d.last_error}</div>}
                </td>
                <td className="px-4 py-2 uppercase text-stone-500">{d.file_type}</td>
                <td className="px-4 py-2 text-stone-500">{fmtSize(d.size_bytes)}</td>
                <td className="px-4 py-2 text-stone-500">{d.page_count ?? "—"}</td>
                <td className="px-4 py-2 text-stone-500">{d.chunk_count}</td>
                <td className="px-4 py-2"><StatusBadge doc={d} /></td>
                <td className="px-4 py-2 text-stone-500">{new Date(d.created_at).toLocaleString()}</td>
                <td className="whitespace-nowrap px-4 py-2 text-right">
                  <button className="btn-ghost px-2" onClick={() => ask(d)} disabled={d.status !== "ready"}>Ask</button>
                  <button className="btn-ghost px-2" onClick={() => setViewing(d)}>View text</button>
                  <button className="btn-ghost px-2" onClick={() => reload(d)} disabled={!!rowBusy[d.id] && rowBusy[d.id] === "Reloading…"}
                    title={d.can_reprocess ? "Re-read the stored file and rebuild its index" : "Rebuild the index from the stored text"}>Reload</button>
                  <button className="btn-ghost px-2 text-red-600" onClick={() => remove(d)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {viewing && <DocumentViewer doc={viewing} onClose={() => setViewing(null)} onChanged={() => load()} />}
    </div>
  );
}
