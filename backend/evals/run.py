"""Run the golden-set evaluation for one or more configurations.

Usage (from backend/):
    python -m evals.run --configs A B           # real run: needs COHERE_API_KEY + ANTHROPIC_API_KEY
    python -m evals.run --configs A B C D       # include the ablations
    python -m evals.run --offline --configs A B # plumbing check with fake providers (NOT real quality numbers)

Judge metrics (faithfulness, relevancy, correctness, context precision) also
need OPENAI_API_KEY; without it they are reported as null, never guessed.
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
from app.errors import AppError
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
        dense = await container.embedder.embed_documents([c.embed_text for c in chunks])
        await store.upsert(
            chunks,
            dense,
            [doc_sparse(c.embed_text) for c in chunks],
            EVAL_USER,
            [scan_document_text(c.text) for c in chunks],
        )
        report["indexed"][path.name] = len(chunks)
    return report


async def evaluate_config(name: str, settings: Settings, golden: list[dict], judge, concurrency: int) -> dict:
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
                "refused": gen.refused,
                "refusal_reason": gen.refusal_reason,
                "citations": [
                    {"n": c.n, "filename": c.filename, "page": c.page_start, "section": c.section}
                    for c in gen.citations
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
                    await judge.grade(q["question"], gen.answer, [c.text for c in ret.kept], q["reference_answer"])
                )
            return row

    rows = await asyncio.gather(*(one(q) for q in golden))
    return {"config": cfg, "corpus": corpus, "summary": summarise(list(rows)), "rows": list(rows)}


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
    args = parser.parse_args()

    settings = Settings(use_fake_providers=args.offline, qdrant_url=None, qdrant_path=None, use_llm_classifier=False)
    if not args.offline:
        missing = [
            k
            for k, v in (("COHERE_API_KEY", settings.cohere_api_key), ("ANTHROPIC_API_KEY", settings.anthropic_api_key))
            if not v
        ]
        if missing:
            sys.exit(
                f"Missing {', '.join(missing)}. Add them to .env (see .env.example) or run with --offline "
                "to check the harness with fake providers."
            )
    else:
        print(OFFLINE_WARNING, file=sys.stderr)

    judge = None
    if not args.offline and not args.no_judge:
        if settings.openai_api_key:
            from evals.judge import Judge

            judge = Judge(settings)
        else:
            print("OPENAI_API_KEY not set: judge metrics will be null (not computed).", file=sys.stderr)

    golden = load_golden(args.golden)
    results = {name: await evaluate_config(name, settings, golden, judge, args.concurrency) for name in args.configs}
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
            "llm": settings.llm_model,
            "judge": settings.judge_model if judge else None,
        },
        "golden_set": {
            "path": args.golden.name,
            "questions": len(golden),
            "unanswerable": sum(not q["answerable"] for q in golden),
        },
        "configs": args.configs,
        "summary": {n: r["summary"] for n, r in results.items()},
        "results": results,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print_table(results)
    print(f"\nSaved {out.relative_to(ROOT.parent)}" + ("  (OFFLINE - not quality results)" if args.offline else ""))


if __name__ == "__main__":
    asyncio.run(main())
