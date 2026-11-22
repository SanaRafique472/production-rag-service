# EVAL

How the system is evaluated, and where the results go.

> **Status: real results are pending.** The build environment had no Cohere, Anthropic or
> OpenAI API keys, so no live run has been made and **no quality numbers are reported here**.
> Run `make eval` with keys (about 5 minutes and well under USD 1 for configs A and B) and
> paste the printed table into section 5. The only run made so far used offline fake
> providers to prove the harness works; those numbers are deliberately not reported.

## 1. Golden set

`backend/evals/golden_set.jsonl`: **42 questions** over the 9-file corpus described in
`DESIGN.md` §2. Each question has the gold evidence (file, page or section, and a short quote
that must appear in a relevant chunk), the facts a correct answer must contain
(`must_include`) and a reference answer.

| Category | n | What it tests |
| --- | --- | --- |
| single_fact | 17 | one fact from prose |
| reworded | 5 | paraphrase with no word overlap ("doctor's note" → "medical certificate") |
| multi_part | 4 | two facts in one answer |
| table | 3 | values inside tables |
| conflict | 1 | 2026 handbook vs 2024 policy: must mention both |
| multilingual | 5 | Urdu and Arabic questions (cross-lingual over English text, and an Urdu document) |
| **unanswerable** | **7** | CEO name, contractors' leave, dental cover, Paris hotel cap, pets, a fact only in the scanned memo, head office city (Urdu) |

## 2. Configurations compared

| Config | Chunking | Search | Rerank | Gate |
| --- | --- | --- | --- | --- |
| **A** baseline | fixed 800/120 tokens (LangChain recursive splitter) | dense only | no | cosine ≥ 0.30 |
| **B** proposed | section-aware 400/60 + context header | dense + BM25, RRF | yes | rerank ≥ 0.35 |
| C ablation | as B | dense only | yes | rerank ≥ 0.35 |
| D ablation | as B | dense + BM25, RRF | no | cosine ≥ 0.30 |

Each configuration gets its own fresh Qdrant collection; the corpus is re-ingested with that
configuration's chunking, then all 42 questions are run through the same retrieval and
generation code the API uses.

## 3. Metrics

**Retrieval** (answerable questions; a chunk is relevant if it contains a gold quote):
hit@1/3/5, recall@1/3/5, MRR; context precision and recall by quote match over the chunks sent
to the LLM.

**Answers:** refusal accuracy (refused ⇔ unanswerable), share of unanswerable questions
refused, refusal precision, key-fact correctness (all `must_include` strings present),
citation coverage, invalid citations dropped, p50/p95 latency, average cost.

**LLM judge** (`gpt-6-luna`, a different model family from the generator to avoid
self-preference; set `JUDGE_MODEL` to change): faithfulness, answer relevancy, correctness,
context precision, each 1–5 normalised to 0–1. Without `OPENAI_API_KEY` these are reported as
`null` (shown as "n/a"), never estimated.

**Judge validation:** label about 20 answers yourself (`evals/human_labels_template.csv` →
`evals/human_labels.csv`) and run `python -m evals.agreement evals/results/<run>.json` to get
Cohen's kappa between you and the judge. Above ~0.6 means the judge can be trusted on this set.

## 4. How to run

```bash
cp .env.example .env            # add COHERE_API_KEY, ANTHROPIC_API_KEY (and OPENAI_API_KEY for judge metrics)
make eval                       # = python -m evals.run --configs A B C (from backend/)
cd backend && ../.venv/bin/python -m evals.run --configs A B C D   # include the second ablation
cd backend && ../.venv/bin/python -m evals.run --offline --configs A B   # harness check only, fake providers
```

Each run writes `backend/evals/results/<timestamp>-<mode>.json` (summary, per-category
breakdown and every question's answer, citations and retrieved chunks) and prints a Markdown
table. The **Evaluation** page in the app lists the runs. Offline runs are labelled and
git-ignored.

## 5. Results

_Not yet run with real providers._ After `make eval`, paste the printed table here and fill in:

| metric | A | B | C |
| --- | --- | --- | --- |
| hit@5 | | | |
| MRR | | | |
| context precision (quote match) | | | |
| refusal accuracy | | | |
| key-fact correctness | | | |
| faithfulness (judge) | | | |
| latency p50 / p95 (ms) | | | |
| avg cost per question (USD) | | | |

## 6. Analysis (to write after the run)

Answer these with the numbers above:

1. Does B beat A on hit@5 / MRR, and is the gain larger for `table` and `reworded` questions?
2. What does BM25 add (B vs C) on exact-term questions (q17 Vaultly, q29 SEV2, q15 mileage)?
3. What does the reranker add (B vs D) on context precision and refusal accuracy?
4. Which unanswerable questions were answered anyway, and why (look at their kept chunks)?
5. Did q30 mention both remote-work rules?
6. Were Urdu/Arabic questions answered in the question's language?
7. Is the extra latency/cost of reranking worth the quality gain?

Pick the final configuration from these answers and record the reason in `DESIGN.md`.
