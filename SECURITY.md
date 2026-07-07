# Security Policy

Hunter is local-first. By default, job-search data is stored on the user's machine in `data/hunter.sqlite`, and provider settings are stored in `data/settings.local.json`.

## Private Data

Do not commit or share:

- `data/hunter.sqlite`
- `data/settings.local.json`
- exports containing real job-search data
- `app/dist/` builds created from a private local session
- resumes, cover letters, contacts, compensation notes, or interview notes
- API keys or provider tokens

Frontend source files are committed app code and should not contain private data. Runtime API responses from the local app server can contain private job-search data. The repository ignores local private artifacts by default. Run this before committing:

```bash
python3 hunter.py repo-check
python3 hunter.py clean-caches
```

## Network Behavior

Hunter may fetch job posting URLs during ingestion. AI-powered action generation only runs when provider settings are configured and the user explicitly invokes an AI action-generation flow.

Do not add new network transmission, telemetry, analytics, sync, or hosted publishing behavior without clear user-facing opt-in behavior.

## Reporting Security Issues

Until the project has a public vulnerability intake address, open a private report with the maintainer or avoid posting sensitive exploit details publicly. Include enough information to reproduce the issue without including real job-search data or tokens.
