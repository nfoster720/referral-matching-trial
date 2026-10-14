VENV := .venv
PY := ../$(VENV)/bin/python
RUFF := ../$(VENV)/bin/ruff
DJANGO_DEBUG ?= 1
export DJANGO_DEBUG

.PHONY: setup db-up db-down seed dev dev-backend dev-frontend test test-backend test-frontend lint

setup: db-up
	python3 -m venv $(VENV)
	$(VENV)/bin/python -m pip install --quiet --upgrade pip
	$(VENV)/bin/pip install --quiet -r backend/requirements.txt
	cd backend && $(PY) manage.py migrate
	cd frontend && npm install
	@echo "\nSetup complete. Next: make seed && make dev"

db-up:
	docker compose up -d --wait

db-down:
	docker compose down

seed:
	cd backend && $(PY) manage.py migrate && $(PY) manage.py seed_trial

dev:
	@trap 'kill 0' INT TERM; \
	( cd backend && $(PY) manage.py runserver 0.0.0.0:8000 ) & \
	( cd frontend && npm run dev ) & \
	wait

dev-backend:
	cd backend && $(PY) manage.py runserver 0.0.0.0:8000

dev-frontend:
	cd frontend && npm run dev

test: test-backend test-frontend

test-backend:
	cd backend && $(PY) -m pytest referrals/tests

test-frontend:
	cd frontend && npm test

lint:
	cd backend && $(RUFF) check .
	cd frontend && npm run lint && npm run typecheck
