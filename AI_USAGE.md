# AI_USAGE

How AI tools were used in this project, what was checked, and what I am responsible for.

## Tools, models and configuration

| | |
| --- | --- |
| Tool | Claude web app (claude.ai), inside a Claude Project, with code execution, file creation and web search turned on |
| Model | Claude Opus 5.5 |
| What it could do | write and run code in a Linux workspace (Python 3.13, Node 22), run tests, notebooks and a headless browser, search the web for current model prices and SDK changes |
| What it could not do | no Docker daemon, no access to my GitHub; no Anthropic or OpenAI keys. I gave it my free Cohere trial key in the chat for the evaluation (kept outside the project files; to be deleted and replaced in the Cohere dashboard after the project) |
| Settings in the app | none special; no fine-tuning, no custom system prompt besides the master prompt below |

## Prompts

The full list of prompts is in [`docs/ai/prompts.md`](docs/ai/prompts.md); the long planning prompt is
[`docs/ai/master_prompt.md`](docs/ai/master_prompt.md).

## Chat history

The complete chat is exported from claude.ai and submitted with the project as
`docs/ai/chat_history.pdf` (export: open the chat → Share → copy link, or print the page to PDF).

## What the AI did

* Analysed the assignment PDF and wrote the project plan (Stage 1 of the master prompt).
* Wrote first versions of the backend, frontend, tests, notebooks, corpus generator, golden set,
  evaluation harness and documentation.
* Ran the tests, linter, notebooks, frontend build and a browser walkthrough, and fixed failures.

## What was verified, and how

| Claim | How it was checked |
| --- | --- |
| Upload, isolation, deletion, guardrails, errors, tracing | 132 automated tests (`make test`), all passing |
| Provider requests/responses are shaped correctly | Contract tests run the real Anthropic, Cohere and OpenAI SDKs against mocked HTTP |
| Fresh install works | New virtual environments on Python 3.11 and 3.13, installed only from `requirements.txt`, then all tests |
| UI works end to end | Signup → upload → ask → monitoring → evaluation clicked through in a headless browser (test mode) |
| Notebooks run | Executed top to bottom with outputs saved |
| Prices and model facts in DESIGN.md | Looked up on the web in November 2026 |

### Problems the checks caught

* The Anthropic SDK (API version 2026-10-21) **rejects `temperature`**: found by a contract
  test and removed.
* The Anthropic SDK now uses `httpx2`, not `httpx`.
* A React effect returned a value from `scrollIntoView`, which crashed the chat page: found in
  the browser check.
* Two-page PDFs kept their running header as a chunk: found while inspecting corpus chunks.
* `requirements.txt` was missing `greenlet` (needed by SQLAlchemy's async engine); it only worked
  in the build environment because the package happened to be installed there. Found by
  installing into a brand-new virtual environment, which is now part of the checks.
* My computer has Python 3.11, but `numpy==2.5.5` and one `def f[T]()` line needed Python 3.12+.
  Fixed (numpy 2.4.6, `TypeVar`) and tested on both Python 3.11 and 3.13; CI now runs both.
* My Anthropic account had no API credit, so the answer model was switched to Cohere Command A+
  (free on a trial key). A live call showed that turning its "thinking" off made it write its
  reasoning into the answer, and that it adds file names in brackets; both are handled in code
  and covered by contract tests.
* The first live evaluation showed the LLM judge marking wrong refusals as correct (EVAL.md §7).
  The numbers were reported as they came out, and the judge prompt was fixed afterwards.

## What was NOT verified by the AI

* The evaluation numbers in `EVAL.md` come from one real run (Cohere, 23 November 2026) made
  by the AI in its workspace with my key; nothing was estimated or edited. The judge scores
  still need my human-label check (`evals/human_labels_to_fill.csv`).
* No run with PostgreSQL or a Qdrant server (no Docker), and none with Claude or OpenAI (no
  credit); those paths are covered by contract tests only.
* The Claude and OpenAI model ids (`claude-haiku-5-5`, `gpt-6-luna`) were not confirmed live.

## My responsibility

Before submitting I re-run `make eval` on my own computer to reproduce the numbers; label the
20 answers in `evals/human_labels_to_fill.csv` and run `python -m evals.agreement`; read every
module so I can explain it; and record the demo from `docs/demo_script.md`.
