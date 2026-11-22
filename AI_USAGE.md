# AI_USAGE

How AI tools were used in this project, what was checked, and what I am responsible for.

## Tools, models and configuration

| | |
| --- | --- |
| Tool | Claude web app (claude.ai), inside a Claude Project, with code execution, file creation and web search turned on |
| Model | Claude Opus 5.5 |
| What it could do | write and run code in a Linux workspace (Python 3.13, Node 22), run tests, notebooks and a headless browser, search the web for current model prices and SDK changes |
| What it could not do | no API keys for Cohere/Anthropic/OpenAI, no Docker daemon, no access to my GitHub |
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
| Upload, isolation, deletion, guardrails, errors, tracing | 128 automated tests (`make test`), all passing |
| Provider requests/responses are shaped correctly | Contract tests run the real Anthropic, Cohere and OpenAI SDKs against mocked HTTP |
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

## What was NOT verified by the AI

* **No live run** with real Cohere/Claude/OpenAI keys, PostgreSQL or a Qdrant server. So the
  AI produced **no evaluation numbers**; `EVAL.md` is filled in by me after `make eval`.
* Model ids (`claude-haiku-5-5`, `embed-v5.0-*`, `rerank-v5.0-fast`, `gpt-6-luna`) must be
  confirmed on each provider's models page.

## My responsibility

Before submitting I run `make eval` with my own keys and write the results and failure
analysis in `EVAL.md`; label ~20 answers to check the judge (`python -m evals.agreement`);
read every module so I can explain it; and record the demo from `docs/demo_script.md`.
