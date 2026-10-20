# Agent roles and file ownership

The orchestrator session (the one Jawat talks to) plans, routes questions and checks reports. It does not build.
**Two worker agents at a time** (Pro plan). Two agents never write the same path.

| ID | Role | Model | Owns (writes only here) | Wave |
|---|---|---|---|---|
| R1 | Research: SME selection, datasets + licences, financing products, official rules | Sonnet 5.5 high | `docs/research/`, `docs/LICENSES_DATA.md` | 1 |
| A1 | Core backend architect: scaffold, canonical schema, ledger, agent loop, LLM providers, security module, API | Opus 5.5 | `backend/` except `backend/connectors/` and `backend/engine/`; `pyproject`/requirements; `.env.example` | 1 |
| D1 | Data: seeded generators for 3 SMEs (≥5 yrs), injected data-entry problems, answer keys | Sonnet 5.5 high | `scripts/data/`, `data/synthetic/` | 2 |
| M1 | Engine: Cash Truth reconciliation, metrics/P&L, forecast + backtest, alerts, financing matcher, scenarios | Opus 5.5 | `backend/engine/`, `tests/engine/` | 3 |
| C1 | Connectors: 6 live (CSV/Excel, Google Sheets, QuickBooks sandbox, bank CSV, wallet CSV, one live POS API) + POS export profiles with sample files + `docs/CONNECTOR_ROADMAP.md` | Sonnet 5.5 high | `backend/connectors/`, `tests/connectors/` | 3 |
| F1 | Frontend (Next.js), incl. weekly Owner Brief view with approve/reject | Sonnet 5.5 high | `frontend/` | 4 |
| P1 | ~~Real-world pilot~~ — **cancelled 19 Oct** (no private data in this project) | — | — | — |
| Q1 | **Eval + security (merged with S1)**: 30–40 scripted tasks by one command scored against answer keys; red-team scoreboard (40+ attacks); security self-assessment, permission tiers, known-risk list; evidence pack | Sonnet 5.5 high | `eval/`, `tests/scenarios/`, `tests/security/`, `docs/evidence/`, `docs/security/` | 5 |
| W1 | Deliverables: technical doc, deck, demo-video script, README polish | Sonnet 5.5 medium | `docs/deliverables/`, `README.md` | 6 |
| V1 | Independent reviewer (did not build anything) | Opus 5.5 | `docs/review/` only | 6 |

## Rules every agent follows
1. Read `CLAUDE.md` and `docs/PLAN.md` first.
2. Work through your whole queue without stopping between steps.
3. **Questions go to the orchestrator, never to Jawat directly.** Send them with SendMessage to the orchestrator session, keep doing any work that does not depend on the answer, and pause only when nothing else is left.
4. Do not commit, push, switch branches or touch files you do not own. The orchestrator commits checkpoints.
5. Never read, print or ask for secrets. Never enter credentials or create accounts.
6. Report when done: what was built (paths), how it was verified (commands + results), what is open. Keep computed facts separate from assumptions.

## Contracts between agents
- Canonical schema: `backend/ledger/schema.sql` (owner A1). D1, C1 and M1 read it; changes go through A1.
- Engine function signatures: `backend/engine/interfaces.py` — **drafted by A1 in wave 1**, ownership passes to M1 when M1 starts. A1's agent tools call these.
- Connector interface: `backend/connectors/base.py` — **drafted by A1 in wave 1**, ownership passes to C1 when C1 starts.
- Synthetic data layout and answer keys: `data/synthetic/README.md` (owner D1).

## Ownership handovers (19 Oct)
- A1 is finished. **C1 takes over these small A1 items:** in `tests/core/test_api.py::test_report_endpoints_return_501_until_engine_exists` and `tests/core/test_agent_chat.py::test_engine_tools_report_not_implemented_honestly` use `make_container(engine_factory=BaseEngine)`; add route `/reports/{id}/owner-brief` and a T1 agent tool for `engine.owner_brief`; add connector env var names to `backend/.env.example`. C1 may edit exactly those parts of `backend/api/`, `backend/agent/tools.py`, `tests/core/` and `.env.example`.
