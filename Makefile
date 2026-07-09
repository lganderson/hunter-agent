PYTHON ?= python3
PORT ?= 8010
API_PORT ?= $(PORT)
VITE_PORT ?= 5173
HUNTER ?= $(PYTHON) hunter.py

.PHONY: init list due stats actions ingest migrate-to-sqlite migrate-postings export-csv load-demo-data mcp repo-check clean-caches frontend-install frontend-dev frontend-build serve-app serve-status serve-stop serve-restart serve-ready run

init:
	$(HUNTER) init

list:
	$(HUNTER) list

due:
	$(HUNTER) due

stats:
	$(HUNTER) stats

actions:
	$(HUNTER) actions

ingest:
	$(HUNTER) ingest $(URLS)

migrate-to-sqlite:
	$(HUNTER) migrate-to-sqlite

migrate-postings:
	$(HUNTER) migrate-postings

export-csv:
	$(HUNTER) export-csv

load-demo-data:
	$(HUNTER) load-demo-data --overwrite

mcp:
	$(HUNTER) mcp

repo-check:
	$(HUNTER) repo-check

clean-caches:
	$(HUNTER) clean-caches

frontend-install:
	cd app && npm install

frontend-dev:
	cd app && HUNTER_API_PORT=$(API_PORT) VITE_PORT=$(VITE_PORT) npm run dev

frontend-build:
	cd app && npm run build

serve-app: frontend-build
	$(HUNTER) serve $(PORT)

serve-status:
	$(HUNTER) serve-status $(PORT)

serve-stop:
	$(HUNTER) serve-stop $(PORT)

serve-restart:
	$(HUNTER) serve-restart $(PORT)

serve-ready:
	$(HUNTER) serve-ready $(PORT)

run: serve-app
