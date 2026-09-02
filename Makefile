.PHONY: dev frontend backend migrate test eval build docker-up

dev:
	@echo "Run 'make backend' and 'make frontend' in two terminals"

frontend:
	cd frontend && npm run dev

backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload

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
