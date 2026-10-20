# SME Finance Copilot — backend

FastAPI + DuckDB + pandas. Default LLM provider is a deterministic **mock** ($0); OpenAI is the
default real provider, Anthropic optional. The LLM never computes numbers and nothing moves money.

## Run

Python 3.10+ (project target 3.11; the dev machine currently runs 3.13).

PowerShell (repo root):
```powershell
.\scripts\dev.ps1 setup     # create .venv and install requirements.txt
.\scripts\dev.ps1 test      # pytest -q
.\scripts\dev.ps1 run       # http://127.0.0.1:8000  (docs at /docs)
```

Git Bash (repo root):
```bash
./scripts/dev.sh setup
./scripts/dev.sh test
./scripts/dev.sh run
```

Without the scripts: `.venv\Scripts\python -m uvicorn backend.api.main:app --port 8000`.

Try it (mock provider; `devco` is a tiny fictional fixture, so start with `SEED_DEV_FIXTURE=1`;
the demo SMEs `bd_grocer`, `cn_xborder`, `uk_garden` get their data from D1's loader):
```bash
curl -N -X POST localhost:8000/chat -H "Content-Type: application/json" -d '{"company_id":"devco","message":"What is my cash position?"}'
curl localhost:8000/audit/verify
```

## Environment variables

Copy `backend/.env.example` to `backend/.env` (git-ignored) and fill it in locally. Real
environment variables override the file. Tests never read `.env` and always use the mock.

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock` \| `openai` \| `anthropic` |
| `LLM_MODEL` | — | model id for openai/anthropic (required for those) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | — | provider keys (never logged) |
| `LLM_DAILY_USD_CAP` | `1.00` | hard daily spend cap, checked before every call |
| `LLM_REQUEST_USD_CAP` | `0.10` | hard per-call cap (worst-case estimate) |
| `LLM_MAX_OUTPUT_TOKENS` | `1200` | max output tokens per call |
| `LLM_PRICING_FILE` | `backend/agent/providers/pricing.json` | editable USD/1M-token prices |
| `APP_ENV` / `DEMO_MODE` | `dev` / `0` | `dev`, `demo` or `prod`; outside dev owner actions need `OWNER_API_TOKEN` (fail closed) |
| `COPILOT_PRIVATE_DIR` | — | instance folder outside the repo: `.env`, `companies.json`, `connectors/`, database |
| `COMPANIES_FILE` | `$COPILOT_PRIVATE_DIR/companies.json` | this instance's companies + initial `granted_tier` |
| `CONNECTOR_PLUGIN_DIRS` | `$COPILOT_PRIVATE_DIR/connectors` | extra connector modules (`*.py`), comma-separated |
| `DUCKDB_PATH` | `data/copilot.duckdb` (`copilot.duckdb` in the private dir) | relative to the private dir if set, else the repo root; `:memory:` allowed |
| `SEED_DEV_FIXTURE` | `0` | load the tiny fictional company `devco` (tier T2); tests use it |
| `DEFAULT_COMPANY_TIER` | `T1` | tier for companies without an explicit grant |
| `T3_DAILY_CAP` | `20` | max T3 actions per company per tool per day |
| `OWNER_API_TOKEN` | — | bearer token for approve/reject, tier changes, `/admin/reset`; mandatory outside dev |
| `REDACT_NAMES` | — | extra comma-separated names to redact |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | frontend origins |
| `SEED_DEMO_COMPANIES` | `1` | create the fictional demo company rows `bd_grocer`, `cn_xborder`, `uk_garden` |
| `DEMO_SEED_MODULE` | `scripts.data.seed` | D1's data hook, called by `/admin/reset`: a module with `seed(db) -> dict` |
| `COPILOT_TODAY` | — | pin "today" (YYYY-MM-DD) for business logic so scripted runs are reproducible |
| `ADMIN_RESET_ENABLED` | `0` | enable `POST /admin/reset` (localhost only; also needs the owner token if set) |
| `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_ENV`, `QBO_REDIRECT_URI` | `QBO_ENV=sandbox` | QuickBooks sandbox (C1) |

## Module map

```
backend/
  config.py              Settings from env (+ private/.env, backend/.env); secrets hidden from repr/logs
  instance.py            per-instance config: companies.json + tiers, connector plugin dirs, private dir check
  core/                  ids (make_id, ID_PATTERN), canonical JSON, hashing, money formatting
  ledger/                schema.sql, system_schema.sql, SCHEMA.md (plain-words guide)
                         db.py (Database, connect), loader.py (lineage-checked bulk load),
                         queries.py (balances, search), lineage.py (evidence refs -> source rows),
                         dev_seed.py (tiny fictional dev company)
  engine/                interfaces.py (FinanceEngine protocol, BaseEngine stubs, get_engine)
                         types.py (result models)            -> owned by M1 from wave 3
  connectors/            base.py (Connector ABC, metadata, registry) -> owned by C1 from wave 3
  security/              tiers, permissions, approvals, audit (hash chain), redaction,
                         injection guard; README.md describes every control
  agent/                 tools.py (registry, tiers, built-in tools), executor.py (enforcement),
                         loop.py (chat loop + SSE event contract), prompts/system.md,
                         providers/ (base, mock, openai, anthropic, cost guard, pricing.json)
  api/                   main.py (FastAPI app + endpoints), container.py (wiring)
  testing.py             helpers for tests: make_container(), make_client(), parse_sse()
```

## API (summary — full list in `api/main.py` docstring, live docs at `/docs`)

`GET /health` · `GET /companies` · `GET|PUT /companies/{id}/permissions` · `GET /tools` ·
`POST /chat` (SSE) · `GET /approvals` · `POST /approvals/{id}/approve|reject` · `GET /audit` ·
`GET /audit/verify` · `GET /connectors` · `GET /evidence?company_id=&ref=` · `GET /usage` ·
`GET /reports/{company_id}/cash-truth|pnl?month=|kpis|forecast|alerts|financing`,
`POST /reports/{company_id}/scenario` (501 until M1 implements the engine function).

**SSE contract** for `/chat` (`event:` = `type`): `session` → `step` (`kind`: `llm_call`,
`tool_call`, `tool_result`, `guard`) … → `final` (`answer`, `evidence_ids`,
`invalid_evidence_ids`, `unverified_numbers`, `approvals`, `session_id`) or `error`
(`code`: `budget_exceeded`, `provider_error`, `max_steps`, …). Send `session_id` back to continue
a conversation. Details: `agent/loop.py` docstring.

## Scripted tasks (judges' harness and our own suite)

```bash
# start: ADMIN_RESET_ENABLED=1 SEED_DEV_FIXTURE=1 COPILOT_TODAY=2026-10-20 ./scripts/dev.sh run
curl -X POST localhost:8000/admin/reset -H "Content-Type: application/json" -d '{"seed": 42}'
curl -X POST localhost:8000/chat/sync -H "Content-Type: application/json" -d '{"company_id":"devco","message":"What is my cash position?","seed":1}'
```

`POST /chat/sync` returns one JSON object per turn: `answer`, `evidence_ids`, `tool_calls`
(tool, tier, args, status, summary, error, approval_id), `tier_decisions` (tool, tool_tier,
granted_tier, decision = executed | queued_for_approval | denied, reason), `guard_flags`,
`refusals`, `approvals`, `invalid_evidence_ids`, `unverified_numbers`, `session_id`, `error`
(add `?include_events=true` for the raw event list). Pass `session_id` back for multi-turn tasks.

Determinism: `/admin/reset` with a seed wipes the DB (the audit chain restarts, its first row
records the previous head) and reloads the fixtures + D1's hook. A chat turn with `seed`
draws its ids from `seed + audit length`. Same reset seed + same turn seeds + `COPILOT_TODAY`
give byte-identical `/chat/sync` responses with the mock provider (tested in
`tests/core/test_harness.py`).

## Running a private instance

The same code runs a private, self-hosted copilot for one real business; nothing is forked.
1. Create a folder **outside the repo** (or a git-ignored one) and set `COPILOT_PRIVATE_DIR` to it.
2. Put the instance `.env` there; it wins over `backend/.env`. Set `APP_ENV=prod`, `OWNER_API_TOKEN`, `SEED_DEMO_COMPANIES=0`, and the LLM provider, model and caps.
3. List the companies and their initial tiers in `companies.json` (format: `backend/instance.py`). A tier the owner changes later via the API survives restarts.
4. Drop private connector modules into `connectors/` (`@register`, see `backend/connectors/base.py`). They are loaded at startup; a broken one is logged, not fatal.
5. The database defaults to `copilot.duckdb` in that folder, so each instance has its own data and audit chain.
6. Start as usual. `/health` shows `app_env`; startup logs `private_dir_not_ignored` if the folder is inside the repo and not git-ignored.
7. Never commit that folder, and never put real company names or figures in repo files.

## How M1 (engine) plugs in

1. Create `backend/engine/impl.py` with `class Engine(BaseEngine)` implementing the methods of
   `FinanceEngine` (`interfaces.py`), returning the models in `types.py`.
2. That's it: `get_engine()` picks it up; `/reports/*` stop returning 501 and the agent's T1 tools
   (`cash_truth`, `monthly_pnl`, `kpis`, `cashflow_forecast`, `alerts`, `financing_matches`,
   `run_scenario`) return your results to the model.
3. Fill `evidence` with `"table:id"` refs and `summary` with code-formatted sentences
   (`backend.core.util.format_money`). Read-only: return `DraftJournalEntry` proposals; the
   agent's T2 tool `draft_journal_entry` writes them after owner approval.
4. Tests in `tests/engine/`: `make_container(engine_factory=Engine)` gives an in-memory app.

## How C1 (connectors) plugs in

1. Add a module in `backend/connectors/`, subclass `Connector`, set `metadata`, implement
   `test_connection`, `discover`, `pull` (and `write_draft` for sandbox drafts), decorate with
   `@register`. Roadmap entries: `register_planned(ConnectorMetadata(..., status="planned"))`.
2. `pull()` returns DataFrames keyed by canonical table with lineage (`self.lineage(df, file)`);
   load them with `backend.ledger.load_frames(db, result.frames, if_exists="skip")`.
3. The API autodiscovers modules at startup; `GET /connectors` lists them.
4. Draft writes reach your `write_draft()` only through the T2 tool `connector_write_draft`
   after owner approval; return `DraftWriteResult(sandbox=True, ...)`.

## Adding agent tools

`backend/agent/tools.py`: write `handler(ctx, args) -> ToolResult` and register a `ToolSpec`
with a tier and JSON schema (no `company_id` parameter). The executor enforces tier, approval,
T3 caps, audit and injection scanning; registration refuses money-moving tools.
