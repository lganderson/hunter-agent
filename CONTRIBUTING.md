# Contributing

Hunter is a local-first job-search companion. Contributions should keep the app easy to clone, private by default, and dependency-light.

## Development Setup

```bash
python3 hunter.py init
make frontend-install
make serve-app
```

Open `http://127.0.0.1:8010/`.

## Validation

Run the complete local check from the repository root:

```bash
make check
```

This runs Python tests with an isolated data root, frontend unit tests, TypeScript checks, a production build, mocked browser tests, real SQLite/browser integration tests, and repository hygiene checks. Install the Chromium test browser once if it is missing:

```bash
cd app
npx playwright install chromium
```

The integration server creates and removes a temporary fictional workspace. It does not use `data/hunter.sqlite` or configured provider credentials. Test failures retain browser traces under ignored `app/test-results/`.

## Screenshots and demo

```bash
make screenshots
```

This builds the frontend and captures `docs/screenshots/` from a disposable demo on port 4176. It permits browser requests only to that demo. Never capture your personal workspace for public documentation.

To explore the demo interactively, build the frontend and run `python3 scripts/demo_preview.py`, then open `http://127.0.0.1:4175/`.

See [the usage guide](docs/usage.md) for CLI commands, worktrees, imports, exports, and server management.

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
