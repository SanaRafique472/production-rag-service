.PHONY: install dev test lint notebooks eval

PY := .venv/bin/python

install:            ## create venv and install pinned backend deps
	python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt

dev:                ## run the API with auto-reload
	cd backend && ../$(PY) -m uvicorn app.main:app --reload

test:               ## run the full pytest suite
	cd backend && ../$(PY) -m pytest -q

lint:
	cd backend && ../.venv/bin/ruff check app tests

notebooks:          ## execute all notebooks in place (keeps outputs)
	.venv/bin/jupyter nbconvert --to notebook --execute --inplace notebooks/*.ipynb

eval:               ## one-command evaluation (Phase 7)
	cd backend && ../$(PY) -m evals.run --configs A B C
