# Hunter Open Source Architecture

Hunter should become an easy-to-run, open-source job search bot and companion app while still staying useful as a personal tracker during development.

The product should present as a helpful bot with a clear personality: direct, observant, practical, and focused on keeping the hunt moving. Hunter should notice stale postings, suggest concrete next actions, help prepare materials, and keep the user oriented without pretending to be the applicant or inventing application history.

The important product decision is to split the app into reusable core logic plus multiple product surfaces. The current SQLite-backed local dashboard should remain the local-first surface, not the whole architecture.

## Goals

- Let anyone run the app locally with their own private job-search data.
- Support a hosted or self-hosted web app later without rewriting the tracker logic.
- Support a ChatGPT App/MCP surface where users can invoke Hunter inside ChatGPT to inspect postings, create actions, and update application state.
- Keep personal job data, contacts, notes, resumes, tokens, and exports out of the open-source codebase by default.
- Preserve import/export paths so users can leave the app with their data.

## Product Surfaces

### 1. Local-First App

This is the current mode:

- Local data under `data/`, with optional export folders created only when needed.
- A browser dashboard served from localhost.
- Local scripts for ingestion and action generation.
- Bring-your-own API keys for AI features.

This should stay available because it is the lowest-friction way for a technical user to run the app privately.

### 2. Hosted or Self-Hosted Web App

This is the open-source product shape most users will expect:

- Web UI with real client-side state backed by HTTP APIs.
- Backend API for postings, applications, actions, contacts, interviews, and settings.
- SQLite for local/self-hosted use, with a clean path to Postgres for hosted deployments.
- Auth for user accounts.
- Encrypted provider credentials or server-managed AI billing.
- Import/export for CSV and Markdown.

### 3. Hunter ChatGPT App / MCP Surface

This is the "different product architecture" needed for an OAuth-linked ChatGPT experience.

OpenAI's Apps SDK architecture is built around:

- An MCP server that defines tools, enforces auth, returns structured data, and points tools to UI resources.
- A widget/UI bundle that renders inside ChatGPT.
- The ChatGPT model, which decides when to call tools based on tool metadata.

For Hunter, the MCP server should be another client of the same core tracker service. It should not duplicate application logic.

Candidate MCP tools:

- `search_postings`: read-only search across tracked postings.
- `get_posting`: read-only details for one posting.
- `list_applications`: read-only pipeline summary.
- `list_actions`: read-only task list.
- `create_action`: write a new task.
- `update_action`: complete, defer, or annotate a task.
- `update_application_stage`: move an application through saved, applied, interview, offer, rejected, accepted, and related states.
- `ingest_posting`: add or refresh a posting URL.

Read tools should be marked read-only. Write tools must be idempotent and should require clear current-turn intent before changing user data.

## Authentication Model

There are two separate auth problems.

### App User Auth

For a hosted or ChatGPT-linked app, users need accounts in this product. Use an established identity provider instead of writing auth from scratch. Auth0, Okta, Cognito, Stytch, Clerk, Supabase Auth, or a similar provider can own login, sessions, MFA, password reset, and account recovery.

For the ChatGPT App/MCP surface, the app needs OAuth 2.1 support so ChatGPT can link a user's account and call protected tools. The Apps SDK auth docs describe this as:

- Resource server: this app's MCP server.
- Authorization server: the chosen identity provider.
- Client: ChatGPT acting on behalf of the user.

The MCP server must verify tokens on every protected request: issuer, audience/resource, expiration, and scopes.

### OpenAI API Access

"Sign in with OpenAI" is not the same thing as granting this app OpenAI API spend.

OpenAI API requests use bearer credentials from API keys or workload identity federation. For an open-source user-facing tracker, the practical options are:

- Bring-your-own OpenAI API key for local/self-hosted installs.
- Server-managed OpenAI key for a hosted service, with product-level billing/quotas.
- Workload identity federation only for trusted deployed workloads where the operator controls the OpenAI organization setup.

Do not put OpenAI API keys in browser code, ChatGPT widget state, MCP `structuredContent`, or committed frontend source.

### The Desired "Use My OpenAI Account" Experience

There are two viable interpretations:

1. **Use Hunter inside ChatGPT.** The user is already signed into ChatGPT, opens the Hunter app/bot, and ChatGPT's model calls Hunter's MCP tools. This is the closest match to "sign in with OpenAI and use the bot/app." Hunter supplies tools, structured tracker data, and an app widget; ChatGPT supplies the model experience available to that user in ChatGPT.
2. **Use Hunter as a standalone web app.** The user signs into Hunter, then Hunter calls the OpenAI API. Current OpenAI API documentation points to API keys or workload identity federation for API authentication, not a general third-party OAuth flow where a user signs into OpenAI and delegates their available API models to an external app.

The product should support both surfaces eventually, but they should be described honestly. In the standalone app, the first supported AI auth mode should remain bring-your-own API key or hosted/server-managed billing. In the ChatGPT App, users experience Hunter as the bot/app without Hunter directly receiving their OpenAI API credentials.

If OpenAI later offers a delegated API OAuth flow for third-party applications, Hunter can add it as another provider credential adapter without changing the core tracker model.

## Target Code Shape

A future repo can evolve toward:

```text
apps/
  web/              # Main web app UI.
  api/              # HTTP API for the web app.
  mcp/              # ChatGPT Apps SDK / MCP server.
packages/
  core/             # Posting, application, action, contact, interview domain logic.
  storage/          # Repository interfaces and SQLite/Postgres adapters.
  importers/        # Job posting ingestion and source-specific parsers.
  ai/               # Provider adapters and action-generation prompts.
  ui/               # Shared UI components if web and widget share code.
examples/
  demo-data/        # Safe sample data for screenshots/tests.
docs/
  open-source-architecture.md
```

The current Python scripts can either be migrated into the backend API or kept as command-line tools backed by the same storage layer.

## Data Model Direction

Keep the existing concepts:

- Posting
- Application
- Action
- Contact
- Interview
- Company
- Note/material

Move from CSV-only storage to a repository interface with at least a SQLite implementation. CSV and Markdown should remain import/export formats, not the only persistence layer.

Suggested durable tables:

- `postings`
- `applications`
- `actions`
- `contacts`
- `interviews`
- `companies`
- `notes`
- `tags`
- `application_tags`
- `events`
- `provider_credentials`

The `events` table matters because job applications are timeline-driven. Status changes, replies, interviews, rejections, offers, and user actions should be reconstructable later.

## Privacy and Open Source Hygiene

Before publishing:

- Keep real user data out of the repository.
- Provide sample data in `examples/demo-data/`.
- Add setup commands that create local private data files or a local SQLite database.
- Keep `data/*.local.json`, API keys, tokens, resumes, cover letters, contacts, and real notes ignored by git.
- Keep migration/export commands so existing local CSV or Markdown data can be imported into the SQLite store.
- Add a clear security note explaining what leaves the user's machine in local mode.

## Migration Plan

### Phase 0: Current Local MVP

- Keep the SQLite-backed local tracker, dashboard, ingestion, and action-generation scripts working.
- Keep docs that explain the target architecture and trust boundaries.

### Phase 1: Open-Source Packaging

- Add license, contribution guide, sample data, and issue templates.
- Split personal data from committed demo data.
- Add a one-command local setup path.

### Phase 2: Core Domain Layer

- Extract tracker operations from scripts into a reusable core module.
- Add tests for state transitions, action generation, tag handling, and import/export.
- Keep the current dashboard as a client of the extracted core.

### Phase 3: Database Backend

- Expand SQLite storage behind repository interfaces.
- Keep migration from existing CSV and Markdown files.
- Keep CSV/Markdown export.

### Phase 4: Web App

- Expand the current static local web UI and API into cleaner feature modules.
- Add richer local settings, provider configuration, and action workflows.
- Keep a local-only mode that requires no account.

### Phase 5: MCP Prototype

- Expand the current stdio MCP server beyond local tools where useful.
- Add a ChatGPT widget for reviewing postings and actions.
- Test locally with the MCP Inspector, then through an HTTPS tunnel.

### Phase 6: OAuth-Linked ChatGPT App

- Add OAuth 2.1 through an established identity provider.
- Publish protected resource metadata.
- Add scopes for read-only and write tools.
- Verify access tokens in every MCP tool handler.
- Add audit logs for write tools.

### Phase 7: Hosted Deployment

- Add deployment docs and infrastructure templates.
- Support Postgres and managed object storage.
- Decide whether hosted AI features are bring-your-own-key or server-billed.

## Immediate Next Code Steps

1. Add tests for repository behavior, state transitions, action generation, tag handling, and import/export.
2. Normalize the SQLite model beyond the current CSV-compatible tables where it improves app behavior.
3. Split large dashboard JavaScript into smaller client modules when the local API boundary is stable.
4. Add safe demo data and screenshots that do not use real job-search history.
5. Add OAuth only after the MCP tool contracts and storage boundaries are stable.

## References

- [OpenAI Apps SDK authentication](https://developers.openai.com/apps-sdk/build/auth)
- [OpenAI Apps SDK MCP server guide](https://developers.openai.com/apps-sdk/build/mcp-server)
- [OpenAI API authentication](https://developers.openai.com/api/reference/overview#authentication)
- [OpenAI workload identity federation](https://developers.openai.com/api/docs/guides/workload-identity-federation)
