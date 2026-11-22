import type { AskResponse, Chunk } from "../api";

const fmt = (v: number | null | undefined, d = 3) => (v === null || v === undefined ? "—" : v.toFixed(d));
export const where = (c: { filename: string; page_start: number | null; page_end: number | null; section: string | null }) =>
  c.page_start
    ? `${c.filename}, ${c.page_start === c.page_end ? `p. ${c.page_start}` : `pp. ${c.page_start}–${c.page_end}`}`
    : `${c.filename}${c.section ? `, ${c.section}` : ""}`;

function ChunkCard({ c, highlight }: { c: Chunk; highlight: boolean }) {
  return (
    <div id={`chunk-${c.chunk_id}`}
      className={`rounded-lg border p-3 text-sm transition ${highlight ? "border-brand-600 ring-2 ring-brand-100" : c.kept ? "border-stone-200 bg-white" : "border-stone-100 bg-stone-50 opacity-70"}`}>
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium text-stone-700" dir="auto">{where(c)}</span>
        {c.cited && <span className="rounded bg-brand-100 px-1.5 text-brand-900">cited</span>}
        {c.kept ? <span className="rounded bg-emerald-50 px-1.5 text-emerald-700">sent to LLM</span> : <span className="rounded bg-stone-200 px-1.5 text-stone-600">below threshold</span>}
        {c.kind === "table" && <span className="rounded bg-sky-50 px-1.5 text-sky-700">table</span>}
        {c.suspicious && <span className="rounded bg-amber-100 px-1.5 text-amber-800" title="Contains instructions aimed at AI; treated as data">⚠ instructions</span>}
      </div>
      {c.section && c.page_start && <div className="mb-1 text-xs text-stone-400" dir="auto">{c.section}</div>}
      <p className="line-clamp-5 whitespace-pre-wrap text-stone-600" dir="auto">{c.text}</p>
      <dl className="mt-2 grid grid-cols-4 gap-1 text-[11px] text-stone-500">
        <div><dt>rerank</dt><dd className="font-mono text-stone-800">{fmt(c.rerank_score)}</dd></div>
        <div><dt>cosine</dt><dd className="font-mono text-stone-800">{fmt(c.similarity)}</dd></div>
        <div><dt>dense / bm25</dt><dd className="font-mono text-stone-800">#{c.dense_rank ?? "—"} / #{c.bm25_rank ?? "—"}</dd></div>
        <div><dt>RRF</dt><dd className="font-mono text-stone-800">{fmt(c.rrf_score, 4)}</dd></div>
      </dl>
    </div>
  );
}

export default function EvidencePanel({ res, focus }: { res: AskResponse | null; focus: string | null }) {
  if (!res) return <div className="card p-6 text-sm text-stone-400">Retrieved chunks, scores and citations appear here after you ask a question.</div>;
  const kept = res.retrieved_chunks.filter((c) => c.kept);
  const rest = res.retrieved_chunks.filter((c) => !c.kept);
  return (
    <div className="card space-y-3 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-medium text-stone-800">Evidence</h2>
        <span className="text-xs text-stone-500">
          {res.retrieved_count} retrieved · {res.kept_count} sent to LLM · config {res.retrieval_config}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <div className="rounded-lg bg-stone-50 p-2"><div className="text-stone-500">latency</div><div className="font-mono text-stone-800">{res.latency_ms} ms</div></div>
        <div className="rounded-lg bg-stone-50 p-2"><div className="text-stone-500">tokens in/out</div><div className="font-mono text-stone-800">{res.usage.tokens_in}/{res.usage.tokens_out}</div></div>
        <div className="rounded-lg bg-stone-50 p-2"><div className="text-stone-500">cost</div><div className="font-mono text-stone-800">${res.usage.cost_usd.toFixed(5)}</div></div>
      </div>
      <div className="text-[11px] text-stone-400">
        {Object.entries(res.timings_ms).map(([k, v]) => `${k.replace("_ms", "")} ${v} ms`).join(" · ")}
      </div>
      {kept.length === 0 && <p className="text-sm text-stone-500">No chunk passed the relevance threshold, so the model was not called.</p>}
      <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">
        {kept.map((c) => <ChunkCard key={c.chunk_id} c={c} highlight={c.chunk_id === focus} />)}
        {rest.length > 0 && (
          <details className="pt-1">
            <summary className="cursor-pointer text-xs text-stone-500">{rest.length} more candidate(s) below the threshold</summary>
            <div className="mt-2 space-y-2">{rest.map((c) => <ChunkCard key={c.chunk_id} c={c} highlight={false} />)}</div>
          </details>
        )}
      </div>
    </div>
  );
}
