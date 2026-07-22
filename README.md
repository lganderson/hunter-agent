# Hunter

**Status: Work in progress.** Hunter is a local-first job-search bot and companion app built around a personal job-search workflow. It is public so others can inspect it, adapt it, or run it locally, but the product shape and interfaces are still changing.

Hunter manages postings, applications, contacts, interviews, and actions. Canonical app data lives in a private local SQLite database at `data/hunter.sqlite`, while the repository stays clean for code, docs, and templates.

## Privacy Model

Hunter is private-by-default in local mode:

- Real job-search data stays in ignored local files under `data/`.
- Exports stay in ignored local files under `exports/`.
- Provider/model/token settings stay in `data/settings.local.json`.
- Frontend source and builds must not embed resumes, contacts, compensation notes, application history, or tokens.
- Network access happens only through explicit local actions such as ingesting a posting URL or running AI-powered action generation with configured provider settings.

Before publishing or pushing public changes, use `docs/public-release-checklist.md` and run `python3 hunter.py repo-check`.

## Local Setup

Hunter is designed to keep the backend dependency-light while using a small modern frontend toolchain:

- Python 3.10+.
- Node.js and npm for the React frontend build.
- No required Python packages.
- No external database or hosted service required for the local app.
- SQLite is used through Python's standard library for the local app database.

Run the app:

```bash
python3 hunter.py init
make frontend-install
make serve-app
```

Then open `http://127.0.0.1:8010/`.

The `scripts/` directory still contains direct command wrappers, but `hunter.py` is the preferred local entrypoint.

For a new clone, `init` creates a private local SQLite database at `data/hunter.sqlite`. The React frontend source lives in `app/src/`; private app data is loaded at runtime through the local API.

### Worktree Local Environments

Each git worktree should keep its own ignored local app data, dependencies, and dev ports:

```bash
git worktree add ../hunter-agent-feature -b codex/feature-name main
cd ../hunter-agent-feature
python3 hunter.py init
make frontend-install
make serve-app PORT=8011
```

For frontend development in a worktree, run the API and Vite on matching non-default ports:

```bash
python3 hunter.py serve 8011
make frontend-dev API_PORT=8011 VITE_PORT=5174
```

The Vite dev server reads `HUNTER_API_PORT` for its `/api` proxy and `VITE_PORT` for its own port. Private data stays in that worktree's ignored `data/` directory. Copy `data/hunter.sqlite` or `data/settings.local.json` from another checkout only when you intentionally want that local data in the worktree.

Codex app local environments are configured in `.codex/environments/environment.toml`. When you start a Codex task in a managed worktree and select the Hunter environment, Codex runs the setup script to initialize a fresh local SQLite database, load the fictional demo dataset, install frontend dependencies, build the app, and start a managed local server. The checked-in environment intentionally does not include a `.worktreeinclude` file, so ignored private files such as `data/hunter.sqlite`, `data/settings.local.json`, resumes, logs, and exports are not copied into Codex-managed worktrees unless you add that file deliberately.

For QA, load the committed fictional demo dataset into the current worktree's ignored SQLite database:

```bash
python3 hunter.py load-demo-data --overwrite
```

The Codex local environment loads this during worktree setup and also exposes it as the `Load Demo Data` action when you want to reset a worktree. The fixture lives in `demo/hunter-demo-data.json`; it uses the public company names and careers URLs from the local company list, with synthetic `example.invalid` posting candidates, demo contacts, postings, actions, and posting notes.

After frontend or demo-data changes, refresh the worktree server with:

```bash
python3 hunter.py serve-ready 8011
```

That command rebuilds the frontend, chooses the first free port at or above `8011`, starts Hunter in the background, and writes the active URL to `data/hunter-server.url`.

Import older CSV/Markdown data if you have it:

```bash
python3 hunter.py migrate-to-sqlite
python3 hunter.py migrate-postings
```

The frontend, local API, posting notes, and action workflows use `data/hunter.sqlite` as the primary local store. CSV files are supported as import/export artifacts:

```bash
python3 hunter.py export-csv
```

## Repo Hygiene

Hunter is set up so you can keep using the app locally while keeping the repository clean for commits and open-source work.

Ignored private/generated files include:

- `data/*`, except `data/.gitkeep`
- `exports/*`, except `exports/.gitkeep`
- `app/node_modules/` and `app/dist/`
- Python caches and local virtual environments

Useful pre-commit checks:

```bash
python3 hunter.py repo-check
python3 hunter.py clean-caches
```

`repo-check` reports local private/generated files and warns if any private or generated paths are already tracked by git. `clean-caches` removes Python caches and `.DS_Store` files only; it does not delete your SQLite database, settings, frontend source, or exports.

## Open Source Direction

The current app is the local-first MVP. The intended open-source architecture is documented in `docs/open-source-architecture.md`: keep the private local workflow, extract reusable tracker logic, add a real web/API backend, and eventually expose Hunter as a ChatGPT App/MCP surface with OAuth account linking.

The current local runtime architecture is documented in `docs/local-architecture.md`.

## Core Workflow

1. Add an interesting posting from **Postings → Add posting**, with `python3 hunter.py add`, or with `python3 hunter.py ingest`.
2. Create or refresh the posting note in SQLite from `templates/job-posting.md`.
3. Track each meaningful state change with `stage`, `outcome`, and `tags`.
4. Keep the next concrete action and due date filled in for every active application.
5. Review due actions regularly in the dashboard Actions view.

## Common Commands

```bash
python3 hunter.py list
python3 hunter.py due
python3 hunter.py stats
python3 hunter.py add --company "Acme" --role "Product Manager"
python3 hunter.py actions
python3 hunter.py companies list
python3 hunter.py ingest "https://example.com/job"
python3 hunter.py serve 8010
python3 hunter.py serve-restart 8010
python3 hunter.py serve-ready 8011
python3 hunter.py export-csv
python3 hunter.py mcp
make frontend-dev
make frontend-build
```

The underlying scripts are still available:

```bash
python3 scripts/tracker.py list
python3 scripts/tracker.py due
python3 scripts/tracker.py stats
python3 scripts/action_engine.py
python3 scripts/ingest_postings.py "https://example.com/job"
python3 scripts/serve_app.py 8010
```

For local app QA, restart the managed server on the same port instead of
starting a second copy on a new port:

```bash
python3 hunter.py serve-status 8010
python3 hunter.py serve-restart 8010
python3 hunter.py serve-stop 8010
python3 hunter.py serve-ready 8011
```

The managed server writes its PID and log under ignored `data/` files.

List postings with a specific tag:

```bash
python3 scripts/tracker.py list --tag no-reply
```

Ingest or refresh one or more posting URLs:

```bash
python3 scripts/ingest_postings.py \
  "https://example.com/job-1" \
  "https://example.com/job-2"
```

The same command updates an existing row when the URL already exists; it does not create a duplicate for `http` vs `https`, trailing slash, or `utm_*` differences. When ingest records a non-empty company name, Hunter links the posting to an existing managed company by exact name or alias, or creates a new neutral company when none exists. Use the Make shortcut if preferred:

```bash
make ingest URLS="https://example.com/job"
```

Preview what would change without writing:

```bash
python3 scripts/ingest_postings.py --dry-run "https://example.com/job"
```

For dynamic job pages that show different content to command-line fetches than to a real browser, treat the in-app browser as authoritative. The ingest script warns on suspicious closed-page fallbacks and will not auto-archive a row unless you pass `--mark-closed`.

Generate deterministic actions for all tracked postings:

```bash
make actions
```

Use configured AI settings to add posting-specific action suggestions:

```bash
python3 scripts/action_engine.py --use-ai
python3 scripts/ingest_postings.py --use-ai-actions "https://example.com/job"
```

Manage target companies:

```bash
python3 hunter.py companies upsert --name "Acme" --interest-status interested --careers-url "https://example.com/careers"
python3 hunter.py companies check CO0001
python3 hunter.py companies ingest-candidate CP0001
```

Companies are local SQLite records for interest tracking, careers URLs, notes,
contacts, associated postings, and manually reviewed posting candidates.
Career checks normalize and deduplicate extracted jobs before saving them. Each
candidate retains its source platform, source job ID, matched search queries,
work mode, category, description and scoring hashes, normalization warnings,
and verification state. Hunter also records a scan summary for every check so
successful, partial, and failed scans can be distinguished and raw extraction
counts can be compared with unique candidates.

Add a new opportunity:

```bash
python3 scripts/tracker.py add \
  --company "Acme" \
  --role "Product Manager" \
  --url "https://example.com/job" \
  --source "Company site" \
  --tags "referral" \
  --make-note
```

Update an application:

```bash
python3 scripts/tracker.py update A0001 \
  --company "Acme" \
  --stage waiting-response \
  --add-tag no-reply \
  --date-applied today \
  --add-note "Submitted through company portal."
```

Next action labels and dates are driven by action rows. Edit an action due date
or choose a different open action as the posting's next action from the posting
detail page; completing or reopening actions recomputes the posting summary.

## Folder Map

- `data/`: local app data. `hunter.sqlite` is the local app database; CSV files are only portable import/export files.
- `app/`: Vite React TypeScript frontend. `app/src/` is committed source; `app/dist/` is a generated local build.
- `templates/`: reusable note and message templates.
- `exports/`: generated exports or reports.

Posting ingestion saves an immutable local source snapshot in SQLite alongside the editable posting note. Each distinct capture keeps the original and final URL, capture time, HTTP status, readable page text, raw fetched HTML, warnings, and a content hash. Re-ingesting unchanged content is deduplicated; changed source pages remain available as separate saved versions in the posting detail view and company-data exports.

## Workflow Stages

- `needs-direct-url`: source is not yet a canonical employer posting.
- `posting-review`: deciding whether and how to apply.
- `resume-tailoring`: tailoring materials for this role.
- `ready-to-apply`: materials are ready and submission is next.
- `application-submitted`: submitted, but response state is not known yet.
- `waiting-response`: submitted and waiting for employer reply.
- `recruiter-screen`: recruiter or hiring manager screen.
- `first-interview`: first formal interview round.
- `second-interview`: second formal interview round.
- `final-interview`: final loop or onsite equivalent.
- `offer-review`: reviewing an offer.
- `closed`: no longer active; use `outcome` for the reason.

Stages and action types are editable in the Settings view. Archiving a stage or action type removes it from new selections while preserving historical rows that already reference it.

## Closed Outcomes

- `accepted`: offer accepted.
- `declined`: offer declined.
- `rejected`: employer declined or process ended.
- `withdrawn`: you chose not to continue.
- `archived`: no longer active, kept for reference.
- `closed-posting`: posting closed before a useful application state.

## Suggested Tags

Use comma-separated tags for cross-cutting facts that may matter later. The CLI normalizes spaces to hyphens, so `First interview` becomes `first-interview`.

- Response state: `replied`, `no-reply`.
- Interview state: `recruiter-screen`, `first-interview`, `second-interview`, `final-interview`.
- Outcome state: `offer`, `rejected`, `declined`, `accepted`, `withdrawn`.
- Workflow context: `needs-direct-url`, `referral`, `tailored-resume`, `follow-up-sent`.

## Privacy Note

The local database may contain personal contact details, compensation notes, and application history. `data/*` and `exports/*` are ignored by default so the repository can be updated without committing private data.

## Frontend App

Run the local app server:

```bash
make serve-app
```

Then open:

`http://127.0.0.1:8010/`

For frontend development, run the Python API server in one terminal and Vite in another:

```bash
python3 hunter.py serve 8010
make frontend-dev
```

The Vite dev server proxies `/api` to `http://127.0.0.1:8010` by default. For worktrees or parallel dev servers, use `make frontend-dev API_PORT=8011 VITE_PORT=5174`. The built React app loads private postings, actions, contacts, and note bodies from `GET /api/app-state` at runtime. The Settings page stores provider/model/token data in `data/settings.local.json`, which is ignored by git. Tokens are not embedded into frontend source or build files.

## MCP Support

Hunter includes a local stdio MCP server so agents can inspect and update the app without scraping the dashboard UI.

Run it with:

```bash
python3 hunter.py mcp
```

Example MCP server config:

```json
{
  "mcpServers": {
    "hunter": {
      "command": "python3",
      "args": ["/absolute/path/to/hunter-agent/hunter.py", "mcp"],
      "cwd": "/absolute/path/to/hunter-agent"
    }
  }
}
```

Exposed tools:

- `hunter_list_postings`
- `hunter_get_posting`
- `hunter_list_actions`
- `hunter_update_action`
- `hunter_update_application`
- `hunter_ingest_posting`
- `hunter_list_contacts`
- `hunter_upsert_contact`
- `hunter_link_contact`
- `hunter_unlink_contact`
- `hunter_list_companies`
- `hunter_get_company`
- `hunter_upsert_company`
- `hunter_check_company_postings`
- `hunter_link_company_contact`
- `hunter_unlink_company_contact`
- `hunter_ingest_company_candidate`

The MCP server uses the same local SQLite database as the app. The server is local-only and does not transmit data except to the MCP client you connect it to.

## Project Metadata

- License: MIT; see `LICENSE`.
- Contributing guide: `CONTRIBUTING.md`.
- Security and privacy policy: `SECURITY.md`.
