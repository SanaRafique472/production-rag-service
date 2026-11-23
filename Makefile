.PHONY: install dev test lint notebooks eval eval-offline corpus web

PY := .venv/bin/python

install:            ## create venv and install pinned backend deps
	python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt

dev:                ## run the API with auto-reload on :8000
	cd backend && ../$(PY) -m uvicorn app.main:app --reload

web:                ## run the React dev server on :5173
	cd frontend && npm ci && npm run dev

test:               ## run the full pytest suite (no API keys needed)
	cd backend && ../$(PY) -m pytest -q

lint:
	cd backend && ../.venv/bin/ruff check .

notebooks:          ## execute all notebooks in place (keeps outputs)
	.venv/bin/jupyter nbconvert --to notebook --execute --inplace notebooks/*.ipynb

corpus:             ## regenerate the evaluation corpus in data/corpus/
	cd backend && ../$(PY) scripts/build_corpus.py

eval:               ## one-command evaluation (needs COHERE_API_KEY; paced for a free trial key)
	cd backend && ../$(PY) -m evals.run --configs A B --trial

eval-offline:       ## harness check with fake providers (NOT quality numbers)
	cd backend && ../$(PY) -m evals.run --offline --configs A B
