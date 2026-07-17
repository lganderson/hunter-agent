# Local Architecture

Hunter's local app is intentionally small and dependency-light.

## Runtime Requirements

- Python 3.10+.
- No required Python packages.
- Node.js and npm for frontend development and builds.
- No external database or hosted service required.
- SQLite is available through Python's standard library.

## Entry Points

Use `python3 hunter.py ...` for local usage:

```bash
python3 hunter.py init
python3 hunter.py list --limit 5
python3 hunter.py ingest --dry-run https://example.com/job
python3 hunter.py serve 8010
python3 hunter.py export-csv
python3 hunter.py mcp
```

The older files in `scripts/` are still supported as thin wrappers and implementation modules.

## Package Layout

- `hunter/paths.py`: workspace paths and environment-variable root override.
- `hunter/schema.py`: table field lists and shared state constants.
- `hunter/storage.py`: zero-dependency CSV read/write, date, and tag helpers.
- `hunter/sqlite_store.py`: stdlib SQLite backend for local app persistence.
- `hunter/repository.py`: active storage facade. Uses SQLite for runtime data and CSV only when no database has been initialized.
- `hunter/settings.py`: local provider settings in `data/settings.local.json`.
- `hunter/workflow.py`: SQLite-backed workflow stage and action-type definitions.
- `hunter/actions.py`: action storage operations shared by the app server and scripts.
- `hunter/applications.py`: posting/application update operations.
- `hunter/companies.py`: career-site adapters, canonical candidate normalization, deduplication, availability verification, and scan telemetry.
- `hunter/app_state.py`: runtime JSON serialization for the dashboard API.
- `hunter/mcp_server.py`: dependency-free stdio MCP server for agent access.
- `hunter/commands.py`: top-level command dispatcher used by `hunter.py`.

## Frontend Layout

- `app/`: Vite React TypeScript package root.
- `app/src/main.tsx`: frontend bootstrap.
- `app/src/core/`: API client, types, route helpers, formatting, and shared state hooks.
- `app/src/components/`: reusable UI primitives and icons.
- `app/src/dashboard/`: Dashboard route and KPI/chart view.
- `app/src/postings/`: postings list and posting detail management.
- `app/src/actions/`: actions route and action status updates.
- `app/src/contacts/`: contacts route, contact modal, and posting associations.
- `app/src/settings/`: local AI/provider settings and workflow management controls.

## Storage Mode

Hunter uses SQLite as the default local app store. A new clone becomes usable with:

```bash
python3 hunter.py init
```

That command creates `data/hunter.sqlite`. Frontend source in `app/src/` and built files in `app/dist/` do not embed private data.

Hunter can also import older CSV files:

```text
data/applications.csv
data/actions.csv
data/contacts.csv
data/interviews.csv
```

Run this once to import those files into the local database:

```bash
python3 hunter.py migrate-to-sqlite
```

Run this once to import generated posting Markdown notes into SQLite:

```bash
python3 hunter.py migrate-postings
```

Runtime reads and writes use SQLite. CSV remains a portability format and can be refreshed from the database:

```bash
python3 hunter.py export-csv
```

Company career extraction persists two related record types:

- `company_posting_candidates` stores the current candidate plus extraction,
  normalization, scoring, and verification provenance.
- `company_career_scans` stores one summary per check, including adapter type,
  request successes and failures, extracted and unique counts, availability
  changes, verification skips, and structured errors.

The canonical normalization step is shared across adapters. Adapter-specific
code should return the most structured title, URL, location, work mode,
category, source ID, matched query, and description it can find; normalization
then cleans location noise, rejects navigation records, computes provenance
hashes, and deduplicates before persistence and scoring.

The active local workspace only creates `data/`, `app/`, `exports/`, and `templates/`. Folders such as `postings/`, `resumes/`, `cover-letters/`, and `interviews/` are not created by default; those concepts should live in SQLite or be produced later by explicit export workflows.

## Repo Hygiene

Private local files are ignored by default:

- `data/*`, except `data/.gitkeep`
- `exports/*`, except `exports/.gitkeep`

Run this before committing:

```bash
python3 hunter.py repo-check
python3 hunter.py clean-caches
```

The cleanup command removes Python caches and `.DS_Store` files only. It does not delete the SQLite database, settings, frontend source, or exports.

## App Flow

```text
python3 hunter.py serve 8010
  -> scripts/serve_app.py
  -> serves the built React app from app/dist at /
  -> exposes local JSON endpoints under /api
  -> serves GET /api/app-state from hunter/app_state.py
  -> updates the active repository through hunter/* services
```

The React app uses browser history routes such as `/postings/A0015`. The Python server falls back to `app/dist/index.html` for app routes so those routes can be refreshed directly. Mutating app actions, such as saving settings or completing actions, require the local app server.

## Frontend Development Flow

```text
make frontend-install
make frontend-dev
  -> Vite serves the React app
  -> /api proxies to http://127.0.0.1:8010

make frontend-dev API_PORT=8011 VITE_PORT=5174
  -> Vite serves a parallel worktree frontend on port 5174
  -> /api proxies to that worktree's Hunter server on port 8011

make frontend-build
  -> writes app/dist
  -> python3 hunter.py serve 8010 serves app/dist at /
```

## Worktree Environment Model

Git worktrees share repository history but keep separate working directories. Hunter treats each working directory as its local app root unless `HUNTER_ROOT` or `JOB_HUNT_ROOT` is set. That means every worktree gets separate ignored local files:

- `data/hunter.sqlite`
- `data/settings.local.json`
- `data/hunter-server.pid`
- `data/hunter-server.log`
- `app/node_modules/`
- `app/dist/`

Use different ports when running more than one worktree at the same time:

```bash
python3 hunter.py serve 8011
make frontend-dev API_PORT=8011 VITE_PORT=5174
```

Do not point multiple running worktrees at the same SQLite database. Copy a database into a worktree when you need realistic test data, then treat that copy as disposable unless you intentionally export changes.

Codex app worktrees use the local environment defined at `.codex/environments/environment.toml`. The setup script runs `python3 hunter.py init`, `python3 hunter.py load-demo-data --overwrite`, `make frontend-install`, and `python3 hunter.py serve-ready 8011` in the managed worktree so Codex has a usable demo database, frontend dependencies, and a background local server without copying private local state. Hunter does not check in `.worktreeinclude`; add one only if a future task explicitly needs selected ignored files copied into managed worktrees.

For repeatable QA, `python3 hunter.py load-demo-data --overwrite` loads the committed fixture at `demo/hunter-demo-data.json` into the active worktree's ignored SQLite database. The fixture includes public company names and careers URLs from the local company list while keeping demo postings, contacts, actions, candidates, and notes synthetic. The Codex environment exposes the same command as `Load Demo Data`, so managed worktrees can be seeded without copying private app data from the main checkout.

For repeatable app preview after changes, `python3 hunter.py serve-ready 8011` rebuilds the frontend, chooses the first free port at or above `8011`, starts the managed server in the background, and writes the active URL to `data/hunter-server.url`.

## MCP Flow

```text
MCP client
  -> python3 hunter.py mcp
  -> hunter/mcp_server.py over stdio JSON-RPC
  -> hunter/repository.py and shared update services
  -> data/hunter.sqlite
```

The MCP server exposes tools for listing postings, reading one posting with its note and actions, listing actions, updating actions, updating application tracking fields, and ingesting a posting URL.

## Design Rule

New local functionality should go into `hunter/` first, then be exposed through scripts, the local app server, or future MCP/web surfaces. Avoid adding framework dependencies until a feature clearly needs them.
