"""Deterministic evaluation metrics (no LLM needed).

A retrieved chunk is *relevant* to a gold evidence item when the chunk text
contains the gold quote (after normalising case, spaces and table pipes). All
retrieval metrics are computed on answerable questions only.

* hit@k     - 1 if any gold evidence appears in the top k candidates.
* recall@k  - share of the gold evidence items found in the top k.
* MRR       - 1 / rank of the first relevant candidate (0 if none in the list).
* context precision (quote match) - share of chunks sent to the LLM that contain a gold quote.
* context recall (quote match)    - share of gold evidence items present in the chunks sent to the LLM.
* refusal accuracy - refused when unanswerable, answered when answerable.
* key-fact correctness - the answer contains every ``must_include`` string.
"""

import re
import unicodedata
from statistics import mean

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def norm(text: str) -> str:
    """Lower-case, NFKC, Eastern digits -> ASCII, drop table pipes and markdown dashes, collapse spaces."""
    text = unicodedata.normalize("NFKC", text).translate(_ARABIC_DIGITS).lower()
    text = re.sub(r"\|\s*-{3,}\s*", " ", text).replace("|", " ")
    return re.sub(r"\s+", " ", text).strip()


def relevant_items(chunk_text: str, filename: str, gold: list[dict]) -> set[int]:
    """Indexes of gold items whose quote appears in this chunk (same file)."""
    t = norm(chunk_text)
    return {i for i, g in enumerate(gold) if g["file"] == filename and norm(g["quote"]) in t}


def retrieval_metrics(
    ranked: list[tuple[str, str]], kept: list[tuple[str, str]], gold: list[dict], ks=(1, 3, 5)
) -> dict:
    """Compute hit@k, recall@k, MRR and quote-match context precision/recall for one question."""
    per_rank = [relevant_items(text, fn, gold) for text, fn in ranked]
    out: dict[str, float] = {}
    for k in ks:
        found = set().union(*per_rank[:k]) if per_rank[:k] else set()
        out[f"hit@{k}"] = 1.0 if found else 0.0
        out[f"recall@{k}"] = len(found) / len(gold)
    first = next((i for i, r in enumerate(per_rank, start=1) if r), None)
    out["mrr"] = 1.0 / first if first else 0.0
    kept_rel = [relevant_items(text, fn, gold) for text, fn in kept]
    out["context_precision_qm"] = sum(1 for r in kept_rel if r) / len(kept) if kept else 0.0
    out["context_recall_qm"] = len(set().union(*kept_rel)) / len(gold) if kept_rel else 0.0
    return out


def key_fact_correct(answer: str, must_include: list[str]) -> float:
    """1.0 when every required fact string appears in the answer."""
    a = norm(answer)
    return 1.0 if all(norm(m) in a for m in must_include) else 0.0


def summarise(rows: list[dict]) -> dict:
    """Average per-question metrics into one summary for a configuration."""
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]

    def avg(key: str, items: list[dict]) -> float | None:
        vals = [r[key] for r in items if r.get(key) is not None]
        return round(mean(vals), 4) if vals else None

    retrieval_keys = [k for k in (answerable[0]["retrieval"] if answerable else {})]
    summary = {k: avg(k, [r["retrieval"] for r in answerable]) for k in retrieval_keys}
    correct_refusals = sum(1 for r in rows if r["refused"] != r["answerable"])
    refused = [r for r in rows if r["refused"]]
    summary.update(
        {
            "questions": len(rows),
            "refusal_accuracy": round(correct_refusals / len(rows), 4) if rows else None,
            "unanswerable_refused": round(sum(r["refused"] for r in unanswerable) / len(unanswerable), 4)
            if unanswerable
            else None,
            "answerable_answered": round(sum(not r["refused"] for r in answerable) / len(answerable), 4)
            if answerable
            else None,
            "refusal_precision": round(sum(not r["answerable"] for r in refused) / len(refused), 4)
            if refused
            else None,
            "key_fact_correctness": avg("key_fact_correct", answerable),
            "citation_coverage": avg("has_citation", [r for r in answerable if not r["refused"]]),
            "invalid_citations_dropped": sum(r["invalid_citations"] for r in rows),
            "faithfulness": avg("faithfulness", [r for r in rows if not r["refused"]]),
            "answer_relevancy": avg("answer_relevancy", [r for r in rows if not r["refused"]]),
            "correctness_judge": avg("correctness", answerable),
            "context_precision_judge": avg(
                "context_precision", [r for r in rows if r.get("context_precision") is not None]
            ),
            "latency_p50_ms": _pct([r["latency_ms"] for r in rows], 50),
            "latency_p95_ms": _pct([r["latency_ms"] for r in rows], 95),
            "avg_cost_usd": avg("cost_usd", rows),
        }
    )
    by_category: dict[str, dict] = {}
    for cat in sorted({r["category"] for r in rows}):
        items = [r for r in rows if r["category"] == cat]
        by_category[cat] = {
            "n": len(items),
            "hit@5": avg("hit@5", [r["retrieval"] for r in items if r["answerable"]]),
            "key_fact_correctness": avg("key_fact_correct", [r for r in items if r["answerable"]]),
            "refusal_accuracy": round(sum(1 for r in items if r["refused"] != r["answerable"]) / len(items), 4),
        }
    summary["by_category"] = by_category
    return summary


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[max(-(-p * len(ordered) // 100) - 1, 0)])


def cohen_kappa(a: list[int], b: list[int]) -> float:
    """Agreement between two binary raters beyond chance (1 = perfect, 0 = chance level)."""
    n = len(a)
    if n == 0:
        raise ValueError("no paired labels")
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)
