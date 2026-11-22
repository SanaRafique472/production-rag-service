# AI_USAGE

How AI tools were used in this project, what was checked by hand, and what I am responsible for.

## Tools

* **Claude (Anthropic)** — planning, code generation for the backend, frontend, tests,
  evaluation harness and documentation drafts.

## What the AI did

* Proposed the architecture and the A/B/C/D evaluation configurations from the assignment brief.
* Wrote first versions of the modules, tests, notebooks, corpus generator and golden set.
* Ran the test suite, the linter, the notebooks and the frontend build, and fixed what failed.

## What was verified, and how

| Claim | How it was checked |
| --- | --- |
| Upload, isolation, deletion, guardrails, errors work | 126 automated tests (`make test`), all passing |
| Provider requests/responses are shaped correctly | Contract tests run the real Anthropic, Cohere and OpenAI SDKs against mocked HTTP |
| UI works end to end | Clicked through signup → upload → ask → monitoring → evaluation in a headless browser (test mode) |
| Notebooks run | Executed top to bottom with outputs saved |

### Problems the checks caught

* The Anthropic SDK (2.x / API version 2026-10-21) **rejects `temperature`** — found by a
  contract test and removed.
* The Anthropic SDK now uses `httpx2`, not `httpx`.
* A React effect returned the Promise from `scrollIntoView`, crashing the chat page — found in
  the browser check.
* Two-page PDFs kept their running header in a chunk — found while inspecting corpus chunks.

## What was NOT verified

* **No live run** with real Cohere/Claude/OpenAI keys, PostgreSQL or a Qdrant server (none were
  available in the build environment). Therefore **`EVAL.md` contains no quality numbers yet** —
  none were invented. The offline-mode run only proves the harness executes.
* Model ids (`claude-haiku-5-5`, `embed-v5.0-*`, `rerank-v5.0-fast`, `gpt-6-luna`) must be
  confirmed on each provider's models page before the demo.

## My responsibility

Before submitting I will: run `make eval` with my own keys and fill in `EVAL.md`; label ~20
answers to check the judge (`python -m evals.agreement`); read every module so I can explain it;
and run the demo script against the live stack.
