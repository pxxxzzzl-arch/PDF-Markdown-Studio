.PHONY: setup install install-primary install-ocr frontend test lint run dev macos-app windows-app clean

PYTHON ?= python3

setup: frontend install install-primary

install:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -e '.[dev]'

install-primary:
	.venv/bin/python -m pip install -e '.[primary]'

install-ocr:
	.venv/bin/python -m pip install -e '.[ocr]'

frontend:
	cd frontend && npm ci && npm run build

test:
	.venv/bin/python -m pytest
	cd frontend && npm run build

lint:
	.venv/bin/ruff check src tests scripts

run:
	.venv/bin/pdfmd-server

dev:
	.venv/bin/uvicorn pdfmd.main:app --reload --port 8000

macos-app:
	./scripts/build_macos_app.sh

windows-app:
	powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build_windows_app.ps1

clean:
	find src tests -type d -name __pycache__ -prune -exec rm -r {} +
