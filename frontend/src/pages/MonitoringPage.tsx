import { useEffect, useState } from "react";
import { api, type Metrics, type Trace } from "../api";

const ms = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v)} ms`);
const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card p-4" title={hint}>
      <div className="label">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-stone-800">{value}</div>
    </div>
  );
}

// Simple SVG bar chart: p50 bars per hour with a p95 tick.
function LatencyChart({ data }: { data: Metrics["timeline"] }) {
  if (data.length === 0) return <p className="text-sm text-stone-400">No questions in this window yet.</p>;
  const max = Math.max(...data.map((d) => d.p95_ms ?? 0), 1);
  const w = 640, h = 150, top = 12, bw = Math.min(36, (w - 20) / data.length - 4);
  return (
    <svg viewBox={`0 0 ${w} ${h + top + 24}`} className="h-56 w-full" role="img" aria-label="Latency per hour">
      {data.map((d, i) => {
        const x = 10 + i * ((w - 20) / data.length);
        const p50 = ((d.p50_ms ?? 0) / max) * h, p95 = ((d.p95_ms ?? 0) / max) * h;
        return (
          <g key={d.hour} transform={`translate(0 ${top})`}>
            <title>{`${d.hour}: ${d.questions} questions · p50 ${ms(d.p50_ms)} · p95 ${ms(d.p95_ms)}`}</title>
            <rect x={x} y={h - p50} width={bw} height={p50} rx={3} className="fill-brand-600" />
            <line x1={x - 2} x2={x + bw + 2} y1={h - p95} y2={h - p95} className="stroke-amber-500" strokeWidth={2} />
            <text x={x + bw / 2} y={h + 16} textAnchor="middle" className="fill-stone-400 text-[10px]">{d.hour.slice(11, 13)}h</text>
          </g>
        );
      })}
    </svg>
  );
}

const STEP_COLORS: Record<string, string> = { embed_ms: "bg-sky-500", search_ms: "bg-brand-600", rerank_ms: "bg-violet-500", llm_ms: "bg-amber-500" };

// One row per request: a small waterfall of the steps inside it (embed -> search -> rerank -> LLM).
function TraceTable({ traces }: { traces: Trace[] }) {
  const asks = traces.filter((t) => t.route === "/chat/ask");
  if (asks.length === 0) return <p className="text-sm text-stone-400">Ask a question to see its trace here.</p>;
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-stone-500">
        <tr>{["Request id", "Status", "Total", "Steps", "Tokens", "Cost"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
      </thead>
      <tbody>
        {asks.map((t) => (
          <tr key={t.request_id} className="border-t border-stone-100">
            <td className="px-3 py-1.5 font-mono text-xs">{t.request_id}</td>
            <td className={`px-3 py-1.5 ${t.status_code >= 400 ? "text-red-600" : "text-stone-700"}`}>{t.status_code}{t.refused ? " · refused" : ""}{t.error_type ? ` · ${t.error_type}` : ""}</td>
            <td className="px-3 py-1.5 font-mono">{t.latency_ms} ms</td>
            <td className="px-3 py-1.5">
              <div className="flex h-3 w-48 overflow-hidden rounded bg-stone-100" title={Object.entries(t.steps_ms).map(([k, v]) => `${k.replace("_ms", "")}: ${v} ms`).join(" · ")}>
                {Object.entries(t.steps_ms).map(([k, v]) => (
                  <div key={k} className={STEP_COLORS[k] ?? "bg-stone-400"} style={{ width: `${(v / Math.max(t.latency_ms, 1)) * 100}%` }} />
                ))}
              </div>
            </td>
            <td className="px-3 py-1.5 font-mono text-xs">{t.tokens_in}/{t.tokens_out}</td>
            <td className="px-3 py-1.5 font-mono text-xs">${t.cost_usd.toFixed(5)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function MonitoringPage() {
  const [hours, setHours] = useState(24);
  const [m, setM] = useState<Metrics | null>(null);
  const [error, setError] = useState("");
  const [traces, setTraces] = useState<Trace[]>([]);

  useEffect(() => {
    const load = () => {
      api<Metrics>(`/metrics?hours=${hours}`).then(setM).catch((e) => setError(e.message));
      api<Trace[]>("/metrics/traces?limit=50").then(setTraces).catch(() => {});
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [hours]);

  if (error) return <p className="text-red-700">{error}</p>;
  if (!m) return null;
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold text-stone-800">Monitoring</h1>
        <select className="input ml-auto w-auto" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          {[1, 24, 168, 720].map((h) => <option key={h} value={h}>Last {h < 48 ? `${h} h` : `${h / 24} days`}</option>)}
        </select>
      </div>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="Questions" value={String(m.questions)} />
        <Stat label="Latency p50" value={ms(m.latency_p50_ms)} hint="Typical /chat/ask latency" />
        <Stat label="Latency p95" value={ms(m.latency_p95_ms)} hint="Slow tail: 95% of questions are faster" />
        <Stat label="Error rate" value={pct(m.error_rate)} hint="Share of all requests that ended in 5xx" />
        <Stat label="Refusal rate" value={pct(m.refusal_rate)} />
        <Stat label="Avg tokens in / out" value={`${Math.round(m.avg_tokens_in ?? 0)} / ${Math.round(m.avg_tokens_out ?? 0)}`} />
        <Stat label="Avg cost / question" value={m.avg_cost_usd === null ? "—" : `$${m.avg_cost_usd.toFixed(5)}`} />
        <Stat label="Rate-limited (429)" value={String(m.rate_limited)} />
      </div>
      <div className="card p-4">
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="font-medium text-stone-800">Latency per hour</h2>
          <span className="text-xs text-stone-500"><span className="text-brand-700">■</span> p50 · <span className="text-amber-500">—</span> p95</span>
        </div>
        <LatencyChart data={m.timeline} />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="card p-4">
          <h2 className="mb-2 font-medium text-stone-800">Where the time goes (p50)</h2>
          {Object.entries(m.step_p50_ms).map(([step, v]) => {
            const max = Math.max(...Object.values(m.step_p50_ms).map((x) => x ?? 0), 1);
            return (
              <div key={step} className="mb-2 text-sm">
                <div className="flex justify-between text-stone-600"><span>{step.replace("_ms", "")}</span><span className="font-mono">{ms(v)}</span></div>
                <div className="h-2 rounded bg-stone-100"><div className="h-2 rounded bg-brand-600" style={{ width: `${((v ?? 0) / max) * 100}%` }} /></div>
              </div>
            );
          })}
        </div>
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-stone-50 text-left text-stone-500">
              <tr>{["Route", "Count", "p50", "p95", "5xx"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {Object.entries(m.by_route).map(([route, r]) => (
                <tr key={route} className="border-t border-stone-100">
                  <td className="px-3 py-1.5 font-mono text-xs">{route}</td><td className="px-3 py-1.5">{r.count}</td>
                  <td className="px-3 py-1.5">{ms(r.p50_ms)}</td><td className="px-3 py-1.5">{ms(r.p95_ms)}</td><td className="px-3 py-1.5">{r.errors}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="card overflow-x-auto p-4">
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-medium text-stone-800">Request traces (your latest questions)</h2>
          <span className="text-xs text-stone-500">
            {Object.entries(STEP_COLORS).map(([k, c]) => <span key={k} className="mr-2"><span className={`mr-1 inline-block h-2 w-2 rounded-sm ${c}`} />{k.replace("_ms", "")}</span>)}
          </span>
        </div>
        <TraceTable traces={traces} />
        <p className="mt-2 text-xs text-stone-400">Search the JSON logs for a request id to see every log line of that request.</p>
      </div>
      <p className="text-xs text-stone-500">Total estimated provider cost in window: ${m.total_cost_usd.toFixed(4)} · {m.requests} requests · refreshes every 15 s.</p>
    </div>
  );
}
