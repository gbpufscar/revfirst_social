COMPOSE_FILE=deploy/docker-compose.yml
COMPOSE=docker compose -f $(COMPOSE_FILE)

.PHONY: up down migrate test lint build loadtest

up:
	$(COMPOSE) up --build -d

down:
	$(COMPOSE) down --remove-orphans

migrate:
	$(COMPOSE) run --rm app alembic upgrade head

test:
	python3 -m pytest -q

lint:
	python3 -m ruff check src tests

build:
	docker build -f deploy/Dockerfile .

loadtest:
	python3 scripts/loadtest_basic.py --url http://localhost:$${APP_PORT:-18000}/health
