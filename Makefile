# Единые команды проекта. Их же вызывает CI и на них ссылается AGENTS.md.
.DEFAULT_GOAL := help
.PHONY: help setup test lint format check doctor record build clean sync-agent-rules

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
APP := $(VENV)/bin/snapreel

help: ## показать список команд
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

$(PY):
	python3 -m venv $(VENV)
	$(PIP) install --quiet --upgrade pip

setup: $(PY) ## создать окружение и поставить пакет с dev-экстрой
	$(PIP) install --quiet -e '.[dev]'
	@echo "готово: $(APP)"

test: ## прогнать тесты (экран не нужен)
	$(PY) -m pytest -q

lint: ## статические проверки
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

format: ## отформатировать код
	$(PY) -m ruff format src tests
	$(PY) -m ruff check --fix src tests

check: lint test ## то, что гоняет CI

doctor: ## диагностика окружения этой машины
	$(APP) doctor

record: ## записать клип (для ручной проверки)
	$(APP) record

.PHONY: icon
icon: ## перерисовать иконку приложения (нужен Pillow)
	$(PIP) install --quiet pillow
	$(VENV)/bin/python scripts/make-icon.py

build: ## собрать одиночный бинарник в dist/ (нужен pyinstaller)
	$(PIP) install --quiet pyinstaller
	cd packaging && ../$(VENV)/bin/pyinstaller snapreel.spec --noconfirm --distpath ../dist
	@ls -lh dist/snapreel*

sync-agent-rules: ## пересобрать CLAUDE.md и прочее из AGENTS.md
	./scripts/sync-agent-rules.sh

clean: ## убрать окружение, кэши и сборку
	rm -rf $(VENV) build dist .pytest_cache .ruff_cache src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
