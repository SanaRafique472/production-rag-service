# Production RAG Service

Question answering over your own documents: upload PDF, DOCX, PPTX, TXT or MD files, ask
questions in plain language, and get grounded answers with verifiable citations, or a clear
refusal when the documents do not contain the answer.

> Status: in development. Phase 1 (ingestion) complete. See `DESIGN.md` and `EVAL.md` as they
> are filled in.

## Quick start (local)

```bash
cp .env.example .env            # then add your API keys
make install                    # Python venv + pinned dependencies
docker compose up -d postgres qdrant
make test                       # run the test suite
make dev                        # API at http://127.0.0.1:8000/docs (from Phase 4)
```

## Repository layout

| Path | Purpose |
| --- | --- |
| `backend/app/` | FastAPI service: `config.py`, `errors.py`, `ingestion/`, later `retrieval.py`, `generation.py`, `guardrails.py`, `main.py` |
| `backend/tests/` | pytest unit and API tests |
| `notebooks/` | Step-by-step pipeline notebooks (executed, with outputs) |
| `evals/` | Golden set, evaluation configs, runner and saved results |
| `scripts/` | Helper scripts (e.g. sample document generator) |
| `frontend/` | React app (Phase 8) |

## Security

Secrets are read only from environment variables. `.env` is git-ignored; `.env.example` lists
the variables with empty values. A gitleaks pre-commit hook blocks commits containing keys.
