"""Run the golden-set evaluation for one or more configurations.

Usage (from backend/):
    python -m evals.run --configs A B --trial   # real run with a free Cohere trial key (paced, ~15 min)
    python -m evals.run --configs A B           # real run with production keys (faster)
    python -m evals.run --configs A B C D       # include the ablations
    python -m evals.run --offline --configs A B # plumbing check with fake providers (NOT real quality numbers)

Needs COHERE_API_KEY (and ANTHROPIC_API_KEY only when LLM_PROVIDER=anthropic).
Judge metrics (faithfulness, relevancy, correctness, context precision) use the
judge from JUDGE_PROVIDER; if its key is missing they are null, never guessed.
A question that still fails after retries is recorded as an error and left out
of the averages (the error count is reported).
Results are written to evals/results/<timestamp>-<mode>.json and shown in the
app's Evaluation page.
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml

from app.clients.qdrant_store import QdrantStore
from app.clients.sparse import doc_sparse
from app.config import Settings
from app.errors import AppError, UpstreamRateLimitedError
from app.generation import GenerationResult, detect_language, refusal_message
from app.guardrails import scan_document_text
from app.ingestion.chunking import BASELINE, PROPOSED
from app.ingestion.pipeline import process_file
from app.retrieval import RetrievalConfig, Retriever
from app.services.container import build_container
from evals.metrics import key_fact_correct, retrieval_metrics, summarise

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT.parents[1] / "data" / "corpus"
EVAL_USER = "eval-user"
OFFLINE_WARNING = (
    "OFFLINE MODE: fake hashing embedder, overlap reranker and extractive fake LLM. "
    "These numbers only prove the harness runs; they are NOT quality results."
)


def load_golden(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_config(name: str) -> dict:
    return yaml.safe_load((ROOT / "configs" / f"{name}.yaml").read_text(encoding="utf-8"))


async def with_rate_retry(fn, label: str, waits=(20, 40, 60, 90)):
    """Run ``fn``; when the provider rate-limits us, wait and try again (trial keys)."""
    for wait in (*waits, None):
        try:
            return await fn()
        except UpstreamRateLimitedError:
            if wait is None:
                raise
            print(f"  rate-limited during {label}; waiting {wait}s", file=sys.stderr)
            await asyncio.sleep(wait)


async def ingest(container, store: QdrantStore, chunking, files: list[Path]) -> dict:
    """Index the corpus into a fresh collection; returns a report of what was indexed or rejected."""
    await store.ensure_collection()
    report = {"indexed": {}, "rejected": {}}
    for path in files:
        try:
            result = await asyncio.to_thread(
                process_file,
                path.name,
                path.read_bytes(),
                document_id=str(uuid.uuid4()),
                config=chunking,
                settings=container.settings,
            )
        except AppError as exc:
            report["rejected"][path.name] = exc.code
            continue
        chunks = result.chunks
        dense = await with_rate_retry(
            lambda texts=[c.embed_text for c in chunks]: container.embedder.embed_documents(texts),
            f"embedding {path.name}",
        )
        await store.upsert(
            chunks,
            dense,
            [doc_sparse(c.embed_text) for c in chunks],
            EVAL_USER,
            [scan_document_text(c.text) for c in chunks],
        )
        report["indexed"][path.name] = len(chunks)
    return report


async def evaluate_config(
    name: str, settings: Settings, golden: list[dict], judge, concurrency: int, delay: float = 0.0
) -> dict:
    cfg = load_config(name)
    container = build_container(settings)
    store = QdrantStore(settings, collection=f"eval_{name}_{uuid.uuid4().hex[:6]}", dim=container.store.dim)
    rcfg = RetrievalConfig(
        **{
            **RetrievalConfig.from_settings(settings).__dict__,
            **{k: v for k, v in cfg.items() if k in RetrievalConfig.__dataclass_fields__},
        }
    )
    if settings.use_fake_providers:  # fake scores are on a different scale (same rule as the app)
        rcfg = RetrievalConfig(**{**rcfg.__dict__, "rerank_threshold": 0.2, "dense_threshold": 0.1})
    retriever = Retriever(container.embedder, store, container.reranker, rcfg)
    chunking = BASELINE if cfg["chunking"] == "baseline" else PROPOSED

    t0 = time.perf_counter()
    corpus = await ingest(container, store, chunking, sorted(p for p in CORPUS.iterdir() if p.is_file()))
    corpus["ingest_seconds"] = round(time.perf_counter() - t0, 2)
    print(
        f"[{name}] indexed {sum(corpus['indexed'].values())} chunks from {len(corpus['indexed'])} files; "
        f"rejected {corpus['rejected']}",
        file=sys.stderr,
    )

    s, sem = settings, asyncio.Semaphore(concurrency)

    async def one(q: dict) -> dict:
        async with sem:
            try:
                row = await with_rate_retry(lambda: answer_one(q), q["id"])
            except AppError as exc:  # recorded, excluded from averages, counted in the summary
                print(f"  {q['id']} failed: {exc.code}", file=sys.stderr)
                row = {"id": q["id"], "category": q["category"], "question": q["question"], "error": exc.code}
            if delay:
                await asyncio.sleep(delay)  # keeps a trial key under its per-minute limits
            return row

    async def answer_one(q: dict) -> dict:
        start = time.perf_counter()
        lang = detect_language(q["question"])
        ret = await retriever.retrieve(q["question"], EVAL_USER)
        if ret.kept:
            gen = await container.generator.generate(q["question"], ret.kept, lang)
        else:
            gen = GenerationResult(
                refusal_message("no_relevant_chunks", lang), refused=True, refusal_reason="no_relevant_chunks"
            )
        latency = int((time.perf_counter() - start) * 1000)
        cost = (
            gen.tokens_in * s.price_llm_in
            + gen.tokens_out * s.price_llm_out
            + ret.rerank_tokens * s.price_rerank
            + ret.query_tokens * s.price_embed_query
        ) / 1_000_000
        row = {
            "id": q["id"],
            "category": q["category"],
            "language": q["language"],
            "answerable": q["answerable"],
            "question": q["question"],
            "answer": gen.answer,
            "raw_answer": gen.raw_text,  # the model's text before refusal/citation handling
            "refused": gen.refused,
            "refusal_reason": gen.refusal_reason,
            "citations": [
                {"n": c.n, "filename": c.filename, "page": c.page_start, "section": c.section} for c in gen.citations
            ],
            "has_citation": 1.0 if gen.citations else 0.0,
            "invalid_citations": gen.invalid_citations,
            "retrieved": [
                {
                    "filename": c.filename,
                    "page": c.page_start,
                    "rerank": c.rerank_score,
                    "similarity": c.similarity,
                    "kept": c.kept,
                }
                for c in ret.candidates[:10]
            ],
            "kept_count": len(ret.kept),
            "latency_ms": latency,
            "cost_usd": cost,
            "timings_ms": ret.timings_ms,
        }
        if q["answerable"]:
            row["retrieval"] = retrieval_metrics(
                [(c.text, c.filename) for c in ret.candidates], [(c.text, c.filename) for c in ret.kept], q["gold"]
            )
            row["key_fact_correct"] = 0.0 if gen.refused else key_fact_correct(gen.answer, q["must_include"])
        if judge is not None:
            row.update(
                await judge.grade(
                    q["question"], gen.answer, [c.text for c in ret.kept], q["reference_answer"], q["answerable"]
                )
            )
        return row

    rows = list(await asyncio.gather(*(one(q) for q in golden)))
    ok = [r for r in rows if "error" not in r]
    summary = summarise(ok)
    summary["errors"] = len(rows) - len(ok)
    return {"config": cfg, "corpus": corpus, "summary": summary, "rows": rows}


def print_table(results: dict) -> None:
    keys = [
        "hit@1",
        "hit@5",
        "recall@5",
        "mrr",
        "context_precision_qm",
        "context_recall_qm",
        "refusal_accuracy",
        "key_fact_correctness",
        "faithfulness",
        "answer_relevancy",
        "correctness_judge",
        "latency_p50_ms",
        "latency_p95_ms",
    ]
    names = list(results)
    print("\n| metric | " + " | ".join(names) + " |")
    print("|---|" + "---|" * len(names))
    for k in keys:
        vals = [results[n]["summary"].get(k) for n in names]
        print(
            f"| {k} | "
            + " | ".join("n/a" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v) for v in vals)
            + " |"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs", nargs="+", default=["A", "B"])
    parser.add_argument("--golden", type=Path, default=ROOT / "golden_set.jsonl")
    parser.add_argument("--offline", action="store_true", help="use fake providers (harness check only)")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--delay", type=float, default=0.0, help="seconds to wait after each question")
    parser.add_argument(
        "--trial", action="store_true", help="pace for a free Cohere trial key (= --concurrency 1 --delay 7)"
    )
    args = parser.parse_args()
    if args.trial:
        args.concurrency, args.delay = 1, max(args.delay, 7.0)

    settings = Settings(use_fake_providers=args.offline, qdrant_url=None, qdrant_path=None, use_llm_classifier=False)
    if not args.offline:
        needed = [("COHERE_API_KEY", settings.cohere_api_key)]
        if settings.llm_provider == "anthropic":
            needed.append(("ANTHROPIC_API_KEY", settings.anthropic_api_key))
        missing = [k for k, v in needed if not v]
        if missing:
            sys.exit(
                f"Missing {', '.join(missing)}. Add them to .env (see .env.example) or run with --offline "
                "to check the harness with fake providers."
            )
    else:
        print(OFFLINE_WARNING, file=sys.stderr)

    judge = None
    if not args.offline and not args.no_judge:
        judge_key = settings.cohere_api_key if settings.judge_provider == "cohere" else settings.openai_api_key
        if judge_key:
            from evals.judge import Judge

            judge = Judge(settings)
        else:
            print(f"No key for the {settings.judge_provider} judge: judge metrics will be null.", file=sys.stderr)

    golden = load_golden(args.golden)
    results = {
        name: await evaluate_config(name, settings, golden, judge, args.concurrency, args.delay)
        for name in args.configs
    }
    mode = "offline-fake" if args.offline else "live"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = ROOT / "results" / f"{stamp}-{mode}.json"
    out.parent.mkdir(exist_ok=True)
    payload = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": mode,
        "warning": OFFLINE_WARNING if args.offline else None,
        "models": None
        if args.offline
        else {
            "embed": [settings.embed_model_index, settings.embed_model_query],
            "rerank": settings.rerank_model,
            "llm": settings.cohere_llm_model if settings.llm_provider == "cohere" else settings.llm_model,
            "judge": f"{judge.provider}:{judge.model}" if judge else None,
        },
        "golden_set": {
            "path": args.golden.name,
            "questions": len(golden),
            "unanswerable": sum(not q["answerable"] for q in golden),
        },
        "configs": args.configs,
        "judge_rubric": "v2: told whether a refusal is expected",
        "summary": {n: r["summary"] for n, r in results.items()},
        "results": results,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print_table(results)
    print(f"\nSaved {out.relative_to(ROOT.parent)}" + ("  (OFFLINE - not quality results)" if args.offline else ""))


if __name__ == "__main__":
    asyncio.run(main())
