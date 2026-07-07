# Contributing

Hunter is a local-first job-search companion. Contributions should keep the app easy to clone, private by default, and dependency-light.

## Development Setup

```bash
python3 hunter.py init
make frontend-install
make serve-app
```

Open `http://127.0.0.1:8010/`.

## Before Committing

Run:

```bash
python3 hunter.py repo-check
python3 hunter.py clean-caches
python3 -m py_compile hunter.py hunter/*.py scripts/*.py
cd app && npm run typecheck
cd app && npm run build
```

Do not commit local app data:

- `data/hunter.sqlite`
- `data/settings.local.json`
- files under `exports/`
- `app/node_modules/`
- `app/dist/`
- Python caches

## Contribution Guidelines

- Keep local mode usable with Python's standard library.
- Prefer small, reviewable changes.
- Put reusable backend logic in `hunter/` first, then expose it through scripts or the local app server.
- Put frontend route code in `app/src/{dashboard,postings,actions,contacts,settings}` and shared frontend code in `app/src/core` or `app/src/components`.
- Do not add telemetry, sync, publishing, or network transmission of user data without explicit opt-in behavior.
- Avoid committing real job postings, contacts, resumes, compensation notes, interview notes, API tokens, or exports with private app data.

## Reporting Issues

When reporting a bug, include:

- The command or browser action you ran.
- The expected behavior.
- The actual behavior.
- Any traceback or console output with personal data removed.
