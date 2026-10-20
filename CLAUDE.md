# SME Finance Copilot — rules for every agent

FinTechathon 2026, Topic E. Deadline 20 Oct 2026 23:59 Beijing (21:59 Dhaka). Code freeze 20 Oct 12:00 Dhaka.
Plan: `docs/PLAN.md`. Roles and file ownership: `docs/AGENT_ROLES.md`.

## Non-negotiables
- **Write only the paths your role owns.** Do not commit, push or switch branches; the orchestrator does that.
- **Questions go to the orchestrator session via SendMessage**, never to the user directly. Keep working on anything that does not depend on the answer.
- **The LLM never computes numbers.** Every figure comes from a deterministic engine function and can be traced to source rows.
- **No money movement, ever.** Writes to external systems go to sandbox or draft and need owner approval (tier T2).
- **Secrets** live only in `backend/.env` (git-ignored). Never read, print, log or commit them. `.env.example` holds names only.
- **No private or real company data, ever.** Use only synthetic data (scripts/data) and public licensed datasets downloaded into `data/raw/`. Never read files outside this repo otherwise.
- **Licences**: record every dataset and notable dependency in `docs/LICENSES_DATA.md`.
- **Default LLM provider in dev and tests is `mock`** (costs $0). Real providers only for integration checks the orchestrator approves.
- English for all code, docs and UI.

## Stack
Python 3.13 in `.venv` at repo root (code stays 3.10+ compatible), FastAPI, DuckDB, pandas, pytest (backend). Next.js + TypeScript (frontend). Windows dev machine; scripts must work in PowerShell and Git Bash.

## Definition of done for any task
Tests for the new code pass (`pytest` from repo root), the result was checked against the source data rather than against your own output, and the report lists the commands you ran and what they returned.
