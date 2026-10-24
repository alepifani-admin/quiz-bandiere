SHELL := /bin/bash

.PHONY: setup backend-setup frontend-setup backend frontend test test-backend test-frontend seed init data-update stats quality clean

setup: backend-setup frontend-setup

## Backend ------------------------------------------------------------------
backend-setup:
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install -r backend/requirements.txt

backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

## Frontend -----------------------------------------------------------------
frontend-setup:
	source .tools/path.sh && cd frontend && npm install

frontend:
	source .tools/path.sh && cd frontend && npm run dev

## Database & data ----------------------------------------------------------
init:
	cd backend && .venv/bin/python scripts/init_db.py

seed:
	cd backend && .venv/bin/python scripts/update_data.py --source baseline

data-update:
	cd backend && .venv/bin/python scripts/update_data.py --source online

stats:
	cd backend && .venv/bin/python scripts/compute_stats.py

quality:
	cd backend && .venv/bin/python scripts/quality_report.py

## Tests --------------------------------------------------------------------
test-backend:
	cd backend && .venv/bin/python -m pytest -q

test-frontend:
	source .tools/path.sh && cd frontend && npm run test -- --run

test: test-backend
