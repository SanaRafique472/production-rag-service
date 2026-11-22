import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type Doc } from "../api";

const ACCEPT = ".pdf,.docx,.pptx,.txt,.md";
const fmtSize = (b: number) => (b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);

type Status = { name: string; state: "uploading" | "done" | "error"; message?: string };

export default function DocumentsPage() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [uploads, setUploads] = useState<Status[]>([]);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api<{ items: Doc[] }>("/documents?limit=200").then((r) => setDocs(r.items)).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function uploadFiles(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      setUploads((u) => [{ name: file.name, state: "uploading" }, ...u.filter((x) => x.name !== file.name)]);
      const body = new FormData();
      body.append("file", file);
      try {
        const doc = await api<Doc>("/documents", { method: "POST", body });
        const note = doc.flagged_chunks ? ` · ${doc.flagged_chunks} chunk(s) contain instructions aimed at AI and will be treated as data` : "";
        setUploads((u) => u.map((x) => (x.name === file.name ? { ...x, state: "done", message: `${doc.chunk_count} chunks indexed${note}` } : x)));
        load();
      } catch (e) {
        const msg = e instanceof ApiError ? `${e.status} · ${e.message}` : "Upload failed.";
        setUploads((u) => u.map((x) => (x.name === file.name ? { ...x, state: "error", message: msg } : x)));
      }
    }
  }

  async function remove(doc: Doc) {
    if (!confirm(`Delete ${doc.filename}? Its chunks and vectors are removed and it can no longer be cited.`)) return;
    try {
      await api(`/documents/${doc.id}`, { method: "DELETE" });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed.");
    }
  }

  return (
    <div className="space-y-6">
      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); uploadFiles(e.dataTransfer.files); }}
        onClick={() => input.current?.click()}
        className={`card flex cursor-pointer flex-col items-center gap-2 border-2 border-dashed p-10 text-center transition ${drag ? "border-brand-600 bg-brand-50" : "border-stone-300"}`}>
        <p className="font-medium text-stone-700">Drop files here or click to upload</p>
        <p className="text-sm text-stone-500">PDF, DOCX, PPTX, TXT or MD · up to 20 MB · scanned PDFs without a text layer are rejected</p>
        <input ref={input} type="file" multiple accept={ACCEPT} hidden onChange={(e) => e.target.files && uploadFiles(e.target.files)} />
      </div>

      {uploads.length > 0 && (
        <ul className="space-y-1">
          {uploads.map((u) => (
            <li key={u.name} className={`rounded-lg px-3 py-2 text-sm ${u.state === "error" ? "bg-red-50 text-red-700" : u.state === "done" ? "bg-emerald-50 text-emerald-800" : "bg-stone-100 text-stone-600"}`}>
              <span className="font-medium">{u.name}</span> — {u.state === "uploading" ? "processing…" : u.message}
            </li>
          ))}
        </ul>
      )}
      {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 text-left text-stone-500">
            <tr>{["File", "Type", "Size", "Pages", "Chunks", "Uploaded", ""].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody>
            {docs.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-10 text-center text-stone-400">No documents yet. Upload one to start asking questions.</td></tr>
            )}
            {docs.map((d) => (
              <tr key={d.id} className="border-t border-stone-100">
                <td className="px-4 py-2 font-medium text-stone-800" dir="auto">
                  {d.filename}
                  {d.flagged_chunks > 0 && <span title="Contains text that looks like instructions to an AI" className="ml-2 rounded bg-amber-100 px-1.5 text-xs text-amber-800">⚠ {d.flagged_chunks}</span>}
                  {d.empty_pages.length > 0 && <span title="Pages without extractable text" className="ml-2 text-xs text-stone-400">no text on p. {d.empty_pages.join(", ")}</span>}
                </td>
                <td className="px-4 py-2 uppercase text-stone-500">{d.file_type}</td>
                <td className="px-4 py-2 text-stone-500">{fmtSize(d.size_bytes)}</td>
                <td className="px-4 py-2 text-stone-500">{d.page_count ?? "—"}</td>
                <td className="px-4 py-2 text-stone-500">{d.chunk_count}</td>
                <td className="px-4 py-2 text-stone-500">{new Date(d.created_at).toLocaleString()}</td>
                <td className="px-4 py-2 text-right"><button className="btn-ghost text-red-600" onClick={() => remove(d)}>Delete</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
