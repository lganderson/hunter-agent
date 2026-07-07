# Public Release Checklist

Use this before pushing Hunter to a public GitHub repository.

Hunter is a work in progress and is built around a local personal job-search workflow. The public repo should contain reusable code, documentation, templates, and tests only.

## Required Checks

```bash
git status --short
python3 hunter.py repo-check
cd app && npm run typecheck
cd app && npm run build
python3 hunter.py clean-caches
```

Then run:

```bash
git status --short
git ls-files data exports app/dist app/node_modules
git grep -n -I -E "sk-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|OPENAI_API_KEY=|ANTHROPIC_API_KEY=|GOOGLE_API_KEY=|BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY" -- . ':!docs/public-release-checklist.md'
```

Expected results:

- `git status --short` shows only intentional source/doc changes before commit, and nothing after cleanup if no commit is being prepared.
- `repo-check` may list ignored local files, but `Tracked private/generated files` must be `none`.
- `git ls-files data exports app/dist app/node_modules` should list only `data/.gitkeep` and `exports/.gitkeep`.
- The secret-pattern `git grep` should print no real credentials.

## Do Not Commit

- `data/hunter.sqlite`
- `data/settings.local.json`
- `data/resume/`
- `data/agent_usage.jsonl`
- `data/hunter-server.*`
- generated exports or reports under `exports/`
- `app/dist/`
- `app/node_modules/`
- `.env` files
- resumes, cover letters, compensation notes, contact details, interview notes, or real application history

## Public Positioning

Keep the GitHub description broad and WIP-oriented, for example:

> Work-in-progress local-first job search agent and tracker.

Use issue and PR discussions carefully. Redact real companies, contacts, URLs tied to active applications, compensation, and personal timeline details unless they are already intentionally public.
