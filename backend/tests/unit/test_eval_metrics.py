import json
from pathlib import Path

import pytest

from app.ingestion.cleaning import remove_repeated_lines
from app.ingestion.extractors.base import TextUnit
from evals.metrics import cohen_kappa, key_fact_correct, norm, retrieval_metrics, summarise

GOLD = [{"file": "a.pdf", "page": 1, "quote": "12 paid sick days"}, {"file": "a.pdf", "page": 1, "quote": "22 days"}]


def test_norm_handles_tables_and_digits():
    assert norm("| Tier 1 | USD 95 |\n| --- | --- |") == "tier 1 usd 95"
    assert norm("۱۲ دن") == "12 دن"


def test_retrieval_metrics():
    ranked = [("noise", "a.pdf"), ("You get 12 paid sick days.", "a.pdf"), ("22 days of leave", "b.pdf")]
    m = retrieval_metrics(ranked, kept=ranked[:2], gold=GOLD, ks=(1, 3))
    assert m["hit@1"] == 0 and m["hit@3"] == 1 and m["recall@3"] == 0.5  # 22 days is in the wrong file
    assert m["mrr"] == 0.5 and m["context_precision_qm"] == 0.5 and m["context_recall_qm"] == 0.5


def test_key_fact_correct():
    assert key_fact_correct("You get 22 days and 12 sick days", ["22", "12"]) == 1.0
    assert key_fact_correct("You get 22 days", ["22", "12"]) == 0.0


def test_summarise_refusal_accuracy():
    rows = [
        {
            "answerable": True,
            "refused": False,
            "category": "x",
            "retrieval": {"hit@5": 1.0},
            "key_fact_correct": 1.0,
            "has_citation": 1.0,
            "invalid_citations": 0,
            "latency_ms": 10,
            "cost_usd": 0.001,
        },
        {
            "answerable": False,
            "refused": True,
            "category": "y",
            "invalid_citations": 0,
            "latency_ms": 20,
            "cost_usd": 0.0,
        },
        {
            "answerable": False,
            "refused": False,
            "category": "y",
            "invalid_citations": 0,
            "latency_ms": 30,
            "cost_usd": 0.0,
        },
    ]
    s = summarise(rows)
    assert s["refusal_accuracy"] == pytest.approx(2 / 3, abs=1e-3) and s["unanswerable_refused"] == 0.5
    assert s["faithfulness"] is None  # judge not run -> reported as missing, never invented


def test_cohen_kappa():
    assert cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    assert cohen_kappa([1, 1, 0, 0], [1, 0, 1, 0]) == 0.0


def test_golden_set_shape():
    rows = [
        json.loads(line) for line in (Path(__file__).parents[2] / "evals" / "golden_set.jsonl").read_text().splitlines()
    ]
    assert len(rows) >= 25 and sum(not r["answerable"] for r in rows) >= 5
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        assert bool(r["gold"]) == r["answerable"] and r["question"].strip()


def test_two_page_running_header_removed():
    units = [
        TextUnit("Acme - Pricing", page=1),
        TextUnit("Body one.", page=1),
        TextUnit("Acme - Pricing", page=2),
        TextUnit("Body two.", page=2),
    ]
    assert [u.text for u in remove_repeated_lines(units, page_count=2)] == ["Body one.", "Body two."]
