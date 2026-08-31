.PHONY: dev frontend backend test build docker-up

dev:
	@echo "Run 'make backend' and 'make frontend' in two terminals"

frontend:
	cd frontend && npm run dev

backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload

test:
	cd backend && .venv/bin/pytest -q

build:
	cd frontend && npm run build

docker-up:
	docker compose up --build

