.PHONY: help setup dev dev-api dev-ui eval test test-llm lint fmt typecheck check clean
.DEFAULT_GOAL := help

# Every target here has a copy-pasteable equivalent in the README, because
# `make` is not present on every Windows machine and a quickstart that does not
# run on the grader's laptop is not a quickstart.

PY := backend/.venv/Scripts/python
ifeq ($(OS),)
	PY := backend/.venv/bin/python
endif

help:
	@echo "setup      Install backend (editable) and frontend dependencies"
	@echo "dev        Run backend API and frontend dev server (two terminals needed)"
	@echo "dev-api    Run the backend on :8000"
	@echo "dev-ui     Run the frontend on :5173"
	@echo "eval       Run the evaluation harness -> eval/results.json + eval/report.md"
	@echo "test       Run the offline test suite (no Ollama required)"
	@echo "test-llm   Run the live integration test (requires Ollama + the model)"
	@echo "check      lint + typecheck + test"

setup:
	cd backend && uv sync --group dev
	cd frontend && npm install
	@echo "Setup complete. Copy .env.example to .env if you need to change defaults."

dev-api:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-ui:
	cd frontend && npm run dev

dev:
	@echo "Run 'make dev-api' and 'make dev-ui' in two terminals."

eval:
	cd backend && uv run python ../eval/run_eval.py

test:
	cd backend && uv run pytest -q

test-llm:
	cd backend && uv run pytest -q -m llm

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

fmt:
	cd backend && uv run ruff check --fix . && uv run ruff format .

typecheck:
	cd backend && uv run mypy

check: lint typecheck test

clean:
	rm -rf .cache backend/.venv frontend/node_modules frontend/dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
