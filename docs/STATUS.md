# Status and resume guide

Last updated: **17 Oct 2026, evening (Dhaka)**, paused because the laptop is being shut down.
Deadline: **20 Oct 2026 21:59 Dhaka**. Code freeze 20 Oct 12:00 Dhaka. Submit by 20 Oct 18:00 Dhaka.

## How to resume
1. Open the session **"SME Copilot — Orchestrator"** in the Claude desktop app and say "resume". The orchestrator reads this file and continues.
2. If the A1 session didn't finish its pause, open **"A1 Core backend: schema, agent loop, security"** and say "continue your queue". Its notes are in `docs/handover/A1.md` (if written).
3. Start **D1 Data** from its card in the orchestrator chat (task `task_70fdb7c5`, valid for 7 days). If the card is gone, the orchestrator re-creates it from the brief summary below.

## Done (committed)
| Commit | What |
|---|---|
| c672801 | Plan, roles, project rules (`docs/PLAN.md`, `docs/AGENT_ROLES.md`, `CLAUDE.md`) |
| 171a28a | R1 research: official rules, SME selection, datasets + licences, financing catalogue (18 products), connector landscape |
| 915aa85 | SMEs approved; POS must be connection-ready for all relevant POS |
| 62aedd2 | R1: win strategy, POS connector research; Q1 eval role added |
| ce1c0cb | A1: backend skeleton, ledger schema, agent loop, providers (mock/OpenAI/Anthropic), security controls — 81 tests pass (verified by orchestrator) |
| c124887 | Python 3.13 venv rule |

## In progress / queued
| Agent | State | Next |
|---|---|---|
| A1 Core (Opus) | Queue finished (c7029d7, 97 tests pass): POS tables, `/chat/sync`, `/admin/reset`, determinism (`seed`, `COPILOT_TODAY`), the four judge attack scenarios, owner-only approvals | Paused cleanly. On resume (steps in `docs/handover/A1.md`): fail-closed owner-token auth in demo/non-dev mode; `SEED_DEV_FIXTURE` default 0; `SEED_DEMO_COMPANIES` default on (SMEs approved). Then free for integration support |
| R1 Research (Sonnet) | Finished; idle | Nothing queued |
| D1 Data (Sonnet) | **Card waiting for Jawat's click** | 3 SMEs, 2020-01..2026-09, vendor-format source exports, planted problems, `answer_key.json`, `validate.py`, deterministic seed 42 |

### Extra contract for D1 (from A1, pass it on when D1 starts)
- POS schema landed: load order-level data into `pos_orders` / `pos_order_lines` / `pos_payments` (+ optional `pos_shifts`), daily-only exports into `pos_daily_imports`. **Never load `pos_sales_daily`** (it is a derived view; the loader refuses it).
- `pos_payments.method` ∈ cash | card | wallet | aggregator | voucher | bank_transfer | credit | other; `provider` names the brand; amounts signed (+ collected, − refunded). Refunds are separate orders with status='refund'.
- Provide `scripts/data/seed.py` with `seed(db) -> dict`, which A1's `/admin/reset` calls to reload the demo state.

## Agent queue after that (max 2 at once, Pro plan)
1. **M1 Engine (Opus)**: implement `backend/engine/impl.py` (`Engine(BaseEngine)`): Cash Truth reconciliation scored against D1's answer keys, monthly P&L/KPIs, 13-week + 12-month forecast with intervals and backtest error, alerts, financing matcher from `docs/research/financing_catalogue.json` with affordability (DSCR) and plain reasons, scenarios. Needs D1's data.
2. **C1 Connectors (Sonnet)**: connectors per `backend/connectors/base.py`: CSV/Excel with mapping, Google Sheets, QuickBooks Online sandbox (draft writes only after approval), bank CSV, wallet CSV (bKash/Wise style), POS generic adapter + profiles (Odoo live in Docker, Square sandbox live, Loyverse stretch, the rest as export profiles), registry of 30–40 connectors (`docs/research/CONNECTORS_LANDSCAPE.md`, `POS_CONNECTORS.md`).
3. **F1 Frontend (Sonnet)**: Next.js chat + dashboard + Cash Truth inbox + forecast/alerts + financing + approvals + audit viewer + connector registry. SSE contract in `backend/agent/loop.py` docstring.
4. **Q1 Eval (Sonnet)**: 30–40 scripted tasks, one command, scored against answer keys; red-team scoreboard (40+ attacks: induced transfer, data extraction, privilege escalation, injection); evidence pack.
5. **S1 Security (Sonnet)**: security self-assessment, permission-tier write-up, known-risk list, red-team table.
6. ~~P1 pilot~~ cancelled 19 Oct (revision 3: no private data).
7. **W1 Deliverables (Sonnet medium)**: technical doc, deck (≤10 min talk), demo-video script (≤5 min), README.
8. **V1 Reviewer (Opus)**: independent review before the freeze.

## Revision 2 (17 Oct night)
See `docs/PLAN.md` §0b: real-pilot proof first, hero SME = cn_xborder, 6 live connectors, Owner Brief, S1 merged into Q1, Opus only for M1/V1.
**Before the first push:** early local commits contain the pilot company name and the old author identity. Repo identity is now the neutral GitHub noreply (18 Oct). Rewrite/squash all local history (new author on every commit) and run the orchestrator's private-name leak scan on files and author metadata (must be empty) before pushing.

## Next starts (in order, max 2 at once)
1. M1 Engine — card `task_e2d383d9`, start after the usage reset. On start, message M1: also implement `owner_brief(company_id, as_of)` (gap, top 3 alerts, recommended actions with draft ids), and limit scenarios to 3 presets (input cost +15%, lose biggest customer, FX move).
2. R1: public real datasets for validation (revision 3).
3. C1 Connectors, then F1 Frontend, then Q1 Eval + security, W1, V1.

## Waiting on Jawat
- [ ] **Email the organiser** (fintechathon@ican-x.com) with the questions in `docs/research/OFFICIAL_RULES.md` §8, and register the team (students only; passport + enrolment proof).
- [ ] Click the **D1 Data** card.
- [ ] Install **Docker Desktop** (for the Odoo POS).
- [ ] Create free **Square developer** and **Intuit (QuickBooks) developer** accounts; put tokens in `backend/.env` yourself.
- [ ] Get an **OpenAI API key** into `backend/.env` by the morning of 18 Oct (one real-model test run).

- [ ] Confirm POS names: "Clickit" = Klikit? "PyHR" = PiHR? GiantSoft: field list or redacted sample.
- [ ] Optional: redacted sample exports (Bangladeshi POS, foodpanda); a Chinese reader to skim the two Bilibili open-class videos in `WIN_STRATEGY.md`.

## Decisions log
- 17 Oct: Pro plan → max 2 worker agents. FastAPI + Next.js. OpenAI API runtime (no paid Claude API), mock by default.
- 17 Oct: SMEs approved: bd_grocer, cn_xborder, uk_garden. (Pilot cancelled 19 Oct — no private data in this project.)
- 17 Oct: POS connection-ready for all relevant POS. Python 3.13. Owner token required for owner actions in demo mode. devco fixture off by default.
- 17 Oct: Orchestrator may commit locally without asking; never pushes without asking.
