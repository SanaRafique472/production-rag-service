import { useEffect, useState } from "react";
import { api, type EvalRunSummary } from "../api";

const METRICS: [string, string][] = [
  ["hit@1", "Hit@1"], ["hit@5", "Hit@5"], ["recall@5", "Recall@5"], ["mrr", "MRR"],
  ["context_precision_qm", "Context precision (quote match)"], ["context_recall_qm", "Context recall (quote match)"],
  ["refusal_accuracy", "Refusal accuracy"], ["unanswerable_refused", "Unanswerable refused"],
  ["key_fact_correctness", "Key-fact correctness"], ["faithfulness", "Faithfulness (judge)"],
  ["answer_relevancy", "Answer relevancy (judge)"], ["correctness_judge", "Correctness (judge)"],
  ["latency_p50_ms", "Latency p50 (ms)"], ["latency_p95_ms", "Latency p95 (ms)"], ["avg_cost_usd", "Avg cost / question ($)"],
];
const LOWER_IS_BETTER = new Set(["latency_p50_ms", "latency_p95_ms", "avg_cost_usd"]);

function cell(v: unknown, key: string) {
  if (v === null || v === undefined) return <span className="text-stone-400" title="Not computed (judge needs OPENAI_API_KEY)">n/a</span>;
  if (typeof v !== "number") return String(v);
  return key.endsWith("_ms") ? v.toFixed(0) : key === "avg_cost_usd" ? v.toFixed(5) : v.toFixed(3);
}

export default function EvaluationPage() {
  const [runs, setRuns] = useState<EvalRunSummary[] | null>(null);
  const [active, setActive] = useState(0);
  const [error, setError] = useState("");

  useEffect(() => { api<EvalRunSummary[]>("/evals").then(setRuns).catch((e) => setError(e.message)); }, []);

  if (error) return <p className="text-red-700">{error}</p>;
  if (!runs) return null;
  if (runs.length === 0)
    return (
      <div className="card space-y-2 p-8">
        <h1 className="text-lg font-semibold text-stone-800">No evaluation runs yet</h1>
        <p className="text-sm text-stone-600">Run the golden-set evaluation (42 questions, 7 unanswerable) from the project folder:</p>
        <pre className="rounded-lg bg-stone-900 p-3 text-sm text-stone-100">make eval        # needs COHERE_API_KEY and ANTHROPIC_API_KEY (+ OPENAI_API_KEY for judge metrics)</pre>
        <p className="text-sm text-stone-500">Results are saved to <code>backend/evals/results/</code> and shown here.</p>
      </div>
    );

  const run = runs[active];
  const configs = Object.keys(run.summary);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {runs.map((r, i) => (
          <button key={r.run_id} onClick={() => setActive(i)}
            className={`rounded-lg px-3 py-1.5 text-sm ${i === active ? "bg-brand-700 text-white" : "bg-white text-stone-600 shadow-sm"}`}>
            {new Date(r.created_at).toLocaleString()} · {r.mode}
          </button>
        ))}
      </div>
      {run.mode !== "live" && (
        <p className="rounded-lg bg-amber-50 px-4 py-2 text-sm text-amber-800">
          This run used offline fake providers. It shows that the harness works; the numbers are not quality results.
        </p>
      )}
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 text-left text-stone-500">
            <tr><th className="px-4 py-2 font-medium">Metric</th>{configs.map((c) => <th key={c} className="px-4 py-2 font-medium">Config {c}</th>)}</tr>
          </thead>
          <tbody>
            {METRICS.map(([key, label]) => {
              const vals = configs.map((c) => run.summary[c][key]).filter((v): v is number => typeof v === "number");
              const best = vals.length > 1 ? (LOWER_IS_BETTER.has(key) ? Math.min(...vals) : Math.max(...vals)) : null;
              return (
                <tr key={key} className="border-t border-stone-100">
                  <td className="px-4 py-2 text-stone-600">{label}</td>
                  {configs.map((c) => {
                    const v = run.summary[c][key];
                    return <td key={c} className={`px-4 py-2 font-mono ${v === best ? "font-semibold text-brand-700" : "text-stone-800"}`}>{cell(v, key)}</td>;
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-stone-500">
        A = fixed 800-token chunks, dense only · B = section-aware 400-token chunks, hybrid + rerank · C = B without BM25 · D = B without rerank.
        Best value per row in teal. Per-question rows: <code>GET /evals/{run.run_id}</code>.
      </p>
    </div>
  );
}
