# Agent Notes

This is a local job-hunt tracking workspace. Keep edits pragmatic and privacy-aware.

- Treat `data/hunter.sqlite` as private local app data, not a committed source file.
- Use `python3 hunter.py ...` for routine application additions, updates, due actions, stats, ingestion, and local serving.
- The frontend is a Vite React TypeScript app in `app/src/`; use `app/src/core/` for shared frontend code and route folders for route-specific UI.
- Posting note bodies live in SQLite through the `posting_notes` table when the SQLite store is active.
- Do not invent application history, contacts, dates, compensation, or outcomes. Leave unknown fields blank unless the user provides the detail.
- Preserve personal data carefully. Do not add remote publishing, third-party sync, or automation that transmits data without an explicit user request.
- Keep generated/private files out of commits: `data/*`, `exports/*`, `app/node_modules/`, `app/dist/`, caches, and local settings should stay ignored. Frontend source files must not embed private data.
- In Codex-managed worktrees, keep the QA app ready by running `python3 hunter.py serve-ready 8011` after frontend or demo-data changes. It rebuilds, picks an available port, starts the managed local server in the background, and writes the URL to `data/hunter-server.url`.
- Before preparing a commit, run `cd app && npm run typecheck`, `cd app && npm run build`, `python3 hunter.py repo-check`, and `python3 hunter.py clean-caches`.
