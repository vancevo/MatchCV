.PHONY: dev frontend backend worker migrate test eval build docker-up promote-release catalog-sync catalog-check catalog-check-live catalog-eval

QUEUE_URL ?= redis://localhost:6379/0

dev:
	@echo "Run 'make backend' and 'make frontend' in two terminals"

frontend:
	cd frontend && npm run dev

backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload

worker:
	cd backend && .venv/bin/rq worker --url $(QUEUE_URL) talentflow-screening

migrate:
	cd backend && .venv/bin/alembic upgrade head

test:
	cd backend && .venv/bin/pytest -q

eval:
	cd backend && .venv/bin/python -m evals.run_baseline

build:
	cd frontend && npm run build

docker-up:
	docker compose up --build

promote-release:
	cd backend && .venv/bin/python -m scripts.promote_release

# The skill/category catalog TalentFlow and the CV warehouse share lives in shared/catalog.
catalog-sync:
	python3 shared/catalog/sync.py

catalog-check:
	python3 shared/catalog/sync.py --check

catalog-check-live:
	python3 shared/catalog/check_live.py

catalog-eval:
	python3 shared/catalog/evals/run_jd_eval.py
	python3 shared/catalog/evals/run_search_eval.py
