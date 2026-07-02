# Developer convenience targets. (Requires a real Python 3.12 + Redis for run/worker.)
.PHONY: install dev api worker test lint fmt initdb partner reload compose

install:
	pip install -e .[dev]

initdb:
	python -m scripts.manage init-db

api:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	python -m app.workers.worker

partner:
	python -m scripts.manage create-partner --name "Dev Partner" --credits 100000

reload:
	python -m scripts.manage reload

test:
	pytest -q

lint:
	ruff check app scripts tests

fmt:
	ruff check --fix app scripts tests
	ruff format app scripts tests

compose:
	docker compose up --build
