# EVAL

How the system is evaluated, and where the results go.

> **Status: one live run, 23 November 2026** (configs A and B, all 42 questions, no errors).
> Models: Cohere `embed-v5.0-pro` / `embed-v5.0-fast`, `rerank-v5.0-fast`, answers by
> `command-a-plus-05-2026`, judge `command-a-03-2025`, all on a free Cohere trial key.
> Raw results: [`backend/evals/results/20261123-194722-live.json`](backend/evals/results/20261123-194722-live.json)
> (every answer, citation, retrieved chunk and score). Reproduce with `make eval`.

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
| E follow-up | as B | dense + BM25, RRF | yes | rerank ≥ 0.28 |

A and B were run. C, D and E are ready (`backend/evals/configs/`) but were not run, to stay
inside the trial key's 1,000 calls a month (one config costs about 150 calls).

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

**LLM judge** (default Cohere `command-a-03-2025`, an older non-reasoning model, different from
the answer model; `JUDGE_PROVIDER=openai` switches to an OpenAI model): faithfulness, answer
relevancy, correctness, context precision, each 1–5 normalised to 0–1. If the judge's key is
missing these are `null` ("n/a"), never estimated.

**Judge validation:** `backend/evals/human_labels_to_fill.csv` lists 20 answers from the live run
(10 per config, including every failure). Label them, save as `human_labels.csv`, and run
`python -m evals.agreement evals/results/20261123-194722-live.json` to get Cohen's kappa
between the human labels and the judge. Above ~0.6 means the judge can be trusted on this set.
Section 7 shows why this check matters here.

## 4. Why these evaluation tools

| Need | Tool used | Why | Rejected / trade-off |
| --- | --- | --- | --- |
| Retrieval metrics (hit@k, recall@k, MRR) | Our own code (`evals/metrics.py`) | Deterministic, free, no LLM involved; relevance = "chunk contains the gold quote", which anyone can check by hand | — |
| Answer quality (faithfulness, relevancy, correctness, context precision) | **LLM-as-a-judge** (`evals/judge.py`) with a visible 1–5 rubric | Same concepts RAGAS measures, but the prompt and model are pinned and readable in the repo, and it reuses the API's exact retrieval and generation code. Judge model ≠ answer model; free with the same key. Trade-off: same vendor (Cohere), so `JUDGE_PROVIDER=openai` is supported for a fully independent judge. | **RAGAS**: also judges with an LLM, adds a large dependency tree and changes metric prompts between versions; trade-off: our scores are not directly comparable with published RAGAS numbers |
| Cheap, judge-free correctness signal | Key-fact match (`must_include`) | Catches wrong numbers without any LLM cost | Misses correct paraphrases (e.g. "two weeks" vs "14 days"), so it is read together with the judge |
| Judge reliability | Human labels + **Cohen's kappa** (`evals/agreement.py`) | Measures agreement beyond chance; kappa ≥ 0.6 = judge trusted on this set | — |
| Storing and viewing results | JSON in `evals/results/` + Evaluation page in the app | Versioned in Git, reproducible offline, no extra service | **Langfuse** datasets: nice UI, but another hosted service and key for the same table |

## 5. How to run

```bash
cp .env.example .env            # add COHERE_API_KEY (one free trial key is enough)
make eval                       # = python -m evals.run --configs A B --trial  (from backend/, ~30 min)
cd backend && python -m evals.run --configs C D E --trial          # the ablations and the follow-up
cd backend && python -m evals.run --offline --configs A B          # harness check only, fake providers
```

`--trial` runs one question at a time with a 7-second pause, keeping a free key under its
per-minute limits (rerank 10/min, chat 20/min); rate-limited calls wait and retry, and a
question that still fails is recorded as an error and counted, not silently dropped.

Each run writes `backend/evals/results/<timestamp>-<mode>.json` (summary, per-category
breakdown and every question's answer, citations and retrieved chunks) and prints a Markdown
table. The **Evaluation** page in the app lists the runs. Offline runs are labelled and
git-ignored.

## 6. Results

Live run of 23 November 2026, 42 questions per config, 0 errors.

| metric | A (baseline) | B (proposed) |
| --- | --- | --- |
| chunks in the index | 8 | 28 |
| hit@1 / hit@5 | 1.000 / 1.000 | 1.000 / 1.000 |
| recall@1 / recall@5 | 0.986 / 1.000 | 0.971 / 1.000 |
| MRR | 1.000 | 1.000 |
| context precision (quote match) | 0.843 | **0.943** |
| context recall (quote match) | 0.886 | **0.943** |
| refusal accuracy (all 42) | 0.905 | **0.929** |
| unanswerable questions refused (7) | 1.000 | 1.000 |
| answerable questions answered (35) | 0.886 | **0.914** |
| key-fact correctness (answerable) | 0.886 | **0.914** |
| answers with ≥ 1 valid citation | 1.000 | 1.000 |
| invalid citations dropped | 0 | 0 |
| faithfulness (judge) | 1.000 | 1.000 |
| answer relevancy (judge) | 1.000 | 1.000 |
| correctness (judge, rubric v1) | 1.000 | 0.943 |
| latency p50 / p95 (ms) | 1,263 / 1,875 | 1,477 / 1,820 |
| actual cost per question (USD) | 0 | 0 |

Cost is 0 because every call used a free trial key. At list prices only embedding and rerank
would cost anything (≈ USD 0.0003 per question for B; Command A+ itself is free).

By category (key-fact correctness on answerable questions; hit@5 was 1.000 everywhere):

| category | n | A | B |
| --- | --- | --- | --- |
| single_fact | 17 | 0.941 | **1.000** |
| reworded | 5 | 0.600 | **0.800** |
| multi_part | 4 | 1.000 | 1.000 |
| table | 3 | 0.667 | **1.000** |
| conflict | 1 | 1.000 | 1.000 |
| multilingual (Urdu, Arabic) | 5 | **1.000** | 0.600 |
| unanswerable (refusal accuracy) | 7 | 1.000 | 1.000 |

Multilingual (Bonus B): hit@5 was 1.000 for both configs. Every multilingual answer and refusal
was written in the language of the question (checked by hand in the results file: Urdu answers
for q32, q33; Arabic for q34, q35 in A; Urdu/Arabic refusals for q31, q35 in B).

## 7. Failure analysis

**What failed.** A got 4 of 35 answerable questions wrong, B got 3; neither answered a single
unanswerable question.

| config | question | what happened | cause |
| --- | --- | --- | --- |
| A | q07 gifts, q19 doctor's note, q20 roll-over, q27 learning budget | refused although the handbook was the top result | **cosine gate**: with 800-token chunks each document became one chunk, so the handbook's single vector mixes leave, benefits and conduct. Its cosine with these questions was 0.22–0.29, under the 0.30 threshold. |
| B | q19 "off with the flu for three days, doctor's note?" | refused; the right chunk was ranked 1st with rerank 0.318 | **paraphrase + threshold**: no shared words with "medical certificate … more than 2 consecutive days", and 0.318 < 0.35 |
| B | q31 Urdu "how many sick days?" | refused in Urdu; right chunk ranked 1st with rerank 0.345 | **cross-lingual reranking scores lower**; 0.345 missed 0.35 by 0.005. A passed it because its whole-document chunk scored cosine 0.308 ≥ 0.30. |
| B | q35 Arabic "how long is the free trial?" | the right chunk was sent (rerank 0.595) but the reply was the Arabic "not in your documents" refusal | **generation step**, the only failure after retrieval. Either Command A+ used the refusal sentinel, or its answer came back without usable citations and the code replaced it with a refusal (a later live test showed Command A+ sometimes writes citations as raw `<co: 0:[0]>` tags instead of structured citations). This run did not store the raw reply, so it cannot be told which. The judge correctly scored it 0. |

**Answers to the planned questions.**

1. *Does B beat A on retrieval?* Not on hit@k or MRR: both are 1.000, because the corpus is
   small (8 indexed files) and the right document is always ranked first. **This is the main
   limitation of this evaluation**: the golden set does not separate the retrievers; a larger
   corpus with many similar documents is needed. B wins where it matters for answers: context
   precision 0.943 vs 0.843 (less irrelevant text sent to the LLM), key-fact correctness 0.914 vs
   0.886, and all 3 table questions (A missed the learning-budget table, q27).
2. *BM25 (B vs C)* and 3. *reranker (B vs D)*: not measured (C and D not run, see section 2).
   Indirect evidence: in A, without a reranker, 4 answerable questions failed at the cosine
   gate; in B the reranker separated them clearly (answerable gold chunks ≥ 0.318, all 7
   unanswerable questions ≤ 0.198).
4. *Unanswerable questions answered anyway?* None, in either config. In A, q37 (contractors'
   leave) and q40 (pets) passed the cosine gate (0.32, 0.37) and were refused by the LLM itself
   with the sentinel, so the second refusal layer worked. In B none passed the gate.
5. *q30 conflict:* both configs reported both rules (3 days in the 2026 handbook, 2 days in the
   2024 policy) and cited both documents.
6. *Language:* yes, see the end of section 6.
7. *Latency:* B's p50 is 214 ms slower (rerank call), p95 is similar (1.8 s); worth it for the
   gains above.

**Problems with the evaluation itself.**

* **The judge is not reliable for refusals (rubric v1).** For all 4 of A's wrong refusals the
  judge wrote that the answer "correctly refuses … and matches the reference answer" and gave
  full correctness. So A's judge correctness of 1.000 is wrong: by key facts it is 0.886.
  Faithfulness and relevancy are 1.000 everywhere, a ceiling that says little. **Use key-fact
  correctness and refusal accuracy as the primary numbers**, and the judge only as a secondary
  signal until the human-label check (section 3) gives a kappa. Fix made after this run: the
  judge prompt now states whether a refusal is expected (rubric v2, in `evals/judge.py`); it has
  not been re-run yet.
* **Fixes made after this run (not reflected in the numbers above):** raw `<co: …>` citation tags
  are now turned into real citations, bracketed file names are removed, and every row stores the
  model's raw reply (`raw_answer`) so failures like q35 can be diagnosed. The next `make eval`
  measures their effect.
* The gold-quote match cannot see a correct chunk that does not contain the exact quote, so
  context precision is a lower bound.
* One run only; Command A+ is not deterministic, so ±1 question per config is within noise.

**Decision.** Keep **B** as the default: same or better on every answer metric except
multilingual, page-precise citations (A's chunks span whole documents, e.g. pages 1–4 of the
handbook), and tables kept whole. The next change to try is the threshold: every unanswerable
question scored ≤ 0.20 and both false refusals ≥ 0.31, so config **E** (rerank ≥ 0.28) would
likely fix q19 and q31 without new false answers. That conclusion comes from this same golden
set, so it must be confirmed on new questions before E becomes the default; choosing it here
would be overfitting.
