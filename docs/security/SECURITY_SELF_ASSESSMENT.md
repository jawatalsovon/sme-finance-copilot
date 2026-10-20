# Security Self-Assessment — SME Finance Copilot

FinTechathon 2026, Topic E. Scope: the backend in `backend/` (FastAPI, DuckDB), the agent loop, the connectors and the owner app. All data in this submission is synthetic or public (see `docs/LICENSES_DATA.md`); no real company data was used.
Detailed control notes: [`backend/security/README.md`](../../backend/security/README.md). Run evidence: [`docs/evidence/SANDBOX_RUN_LOG.md`](../evidence/SANDBOX_RUN_LOG.md).

**Test result (run for this report):** `pytest -q tests/security_unit` → `65 passed in 8.03s`.

## 1. Security goal and threat model

The copilot reads an SME's books and bank/marketplace data, explains where they disagree, and proposes corrections. The security goal is: **a manipulated, mistaken or malicious model (or user text) must not be able to move money, change books without the owner, or see another company's data — and everything it did must be provable afterwards.**

| Asset | Threat | Actor |
|---|---|---|
| Money / payment instructions | Induced transfer ("CEO says send 50,000 now") | Attacker via chat, e-mail or poisoned file; fooled model |
| Ledger integrity | Unapproved writes, edited approvals | Fooled model; insider with API access |
| Other tenants' data, PII | Data extraction by changing ids or asking | Chat user, model |
| Approval authority | Agent approves its own draft; role escalation | Model, API caller |
| Audit trail | Editing or deleting history | Insider with DB access |
| Secrets / spend | Key leakage, runaway LLM cost | Logs, loops |

Design principle: **the model is not trusted.** It can only call registered tools; tools are checked by deterministic code outside the model; the worst a fooled model can do is bounded by tier and by the owner's approval.

## 2. Permission tiers — where each control lives

| Tier | Meaning | Examples | Enforced in |
|---|---|---|---|
| T0 Read | Read ledger rows, balances, evidence | `account_balances`, `search_transactions` | `backend/security/tiers.py` (`Tier`, `allows`), `backend/agent/executor.py` (`ToolExecutor.execute`) |
| T1 Analyse / suggest | Deterministic engine analyses; suggestions only | `cash_truth`, `kpis`, `cashflow_forecast`, `financing_matches` | same |
| T2 Draft | Writes drafts only (ledger `status='draft'`, connector **sandbox** drafts); every call queued for owner approval | `draft_journal_entry`, `connector_write_draft` | `executor.execute` → `backend/security/approvals.py` (`ApprovalQueue`), `executor.execute_approved` |
| T3 Limited execute | Low-risk, daily-capped, never sends or pays | `mark_reminder_ready` | `executor` (`t3_daily_cap`) |

Other controls and their location:

| Control | Implementation |
|---|---|
| Per-company granted tier | `backend/security/permissions.py` (`PermissionStore`); changes audited as `permission_changed`; set via owner-only `PUT /companies/{id}/permissions` |
| Higher-tier tools hidden from the model | `backend/agent/loop.py` tool list (defence in depth; the executor is the real control) |
| **No money-movement tool exists** | `backend/agent/tools.py` registry; `ToolRegistry.register()` rejects tools flagged `moves_money` or whose name/description looks like pay/transfer/withdraw/top-up/apply-for-credit (`tiers.looks_like_money_movement`, `MoneyMovementForbidden`); connector drafts that are not `sandbox=True` are refused |
| Company scoping | Tools never take `company_id`; the executor binds the session's company; ids supplied by the model are checked to belong to that company (`precheck`, e.g. `_foreign_ids` in `tools.py`); `/evidence` is company-scoped |
| Argument validation | JSON-schema validation, unknown keys rejected (`ToolExecutor.execute`) |
| Approval integrity | `payload_hash = sha256(payload)` stored at request and recomputed before execution (`approvals.verified_payload`); edited payload → `approval_tamper_detected`; tier re-checked at execution; no double decision |
| Owner-only approval | `POST /approvals/{id}/approve|reject` in `backend/api/main.py` behind `require_owner` (bearer `OWNER_API_TOKEN`; fails closed in `demo`/`prod`); actor names that look like the agent (`agent*`, `system*`, `copilot*`, `assistant*`, `llm*`) and the requester itself are refused (`SelfApprovalForbidden`) |
| Money-request policy | `backend/security/policy.py` (`is_money_movement_request`) injects a server policy note; logs `money_movement_refused` |

## 3. Data protection

- **PII redaction before the LLM** (`backend/security/redaction.py`): e-mail, phones, Luhn-valid cards, IBANs, keyworded account/ID numbers, SSN shape, names from the ledger and `REDACT_NAMES`. Reversible placeholders (`[EMAIL_1]`) kept in server memory only; tools run on real values, the provider never sees them. Amounts, dates and evidence refs are left intact so reasoning still works.
- **Untrusted-data envelope** (`backend/security/injection.py`): tool output and loaded text reach the model inside `<untrusted_data>` with `<`/`>` escaped.
- **Numbers are not generated by the LLM**: every figure comes from deterministic engine code; after each answer the loop reports `invalid_evidence_ids` and `unverified_numbers`.
- **Secrets**: only in git-ignored `backend/.env`; `Settings` hides secrets from `repr()` and `public_dict()`; provider errors are reduced to the exception type.
- **Cost cap**: `backend/agent/providers/cost.py` (`GuardedProvider`) enforces `LLM_REQUEST_USD_CAP` and `LLM_DAILY_USD_CAP`; blocked calls are audited (`llm_blocked_budget`).
- **Default provider is `mock`** in dev/tests; pytest forces it and never reads `.env`.

## 4. Audit design

`backend/security/audit.py`: append-only `audit_log`. Every tool call (executed/denied/error), approval request and decision, permission change, LLM call (tokens and cost, **not content**), budget block, injection flag and chat message/answer (SHA-256 of text only) is recorded. `row_hash = sha256(canonical JSON of seq, ts, company_id, actor, action, tool, tier, payload_json, payload_hash, prev_hash)`; each row links to the previous one (`prev_hash`) and `audit_head` pins the latest hash. `GET /audit/verify` (`verify_chain`) detects edited fields, edited payloads (even with a recomputed payload hash), deleted rows, reordered rows and deleted tail rows. The code has no update/delete path. Evidence: the sandbox run ends with `{'ok': True, 'checked': 17, ...}` (see `SANDBOX_RUN_LOG.md`).

## 5. Attack classes → control → proof

Tests live in `tests/security_unit/` (all 65 pass).

| # | Attack | Control | Logged as | Test that proves it |
|---|---|---|---|---|
| 1 | **Induced transfer** ("CEO says transfer 50,000 now") | Policy note on owner text; no money tool exists; unknown tools denied; registry refuses money-moving specs | `money_movement_refused`, `tool_denied` | `test_judge_scenarios.py::test_induced_transfer_is_refused_logged_and_impossible`, `::test_fooled_model_cannot_call_a_transfer_tool`; `test_tiers_and_approvals.py::test_registry_refuses_any_money_moving_tool` |
| 2 | **Data extraction** (other tenant, PII) | Tools never take `company_id`; model-supplied ids checked per company; scoped evidence; PII redacted before LLM | `tool_denied` | `test_judge_scenarios.py::test_cross_company_and_pii_extraction_is_blocked`; `test_tiers_and_approvals.py::test_model_cannot_choose_company_or_unknown_args`; `test_redaction_injection.py` (provider never receives PII) |
| 3 | **Privilege escalation** (T1 doing T2/T3, self-approval) | Tier check in executor; tools hidden; no approve/permission tool; agent-like actors and requester refused; owner token | `tool_denied`, `self_approval_refused`, `owner_auth_failed` | `test_judge_scenarios.py::test_t1_company_cannot_escalate_to_t2_t3_or_admin`, `::test_agent_can_never_approve_its_own_draft`; `test_tiers_and_approvals.py::test_t1_company_cannot_run_t2_tool`, `::test_no_tool_lets_the_agent_approve`; `test_owner_auth.py::test_valid_token_does_not_let_the_agent_approve` |
| 4 | **Prompt injection** (chat text, uploaded files) | `scan_text` on user text and every tool result; `<untrusted_data>` envelope; blast radius limited by tiers + approval | `injection_flagged` | `test_judge_scenarios.py::test_injection_in_chat_text_is_flagged_and_changes_nothing`, `::test_injection_inside_ingested_file_is_flagged_and_contained` |
| 5 | Tampered approval payload | Hash re-verified before execution | `approval_tamper_detected` | `test_tiers_and_approvals.py::test_tampered_approval_payload_is_not_executed` |
| 6 | Tier downgrade after request | Tier re-checked at execution | — | `test_tiers_and_approvals.py::test_tier_downgrade_after_request_blocks_execution` |
| 7 | Unauthenticated owner action | Bearer token; fail closed outside dev | `owner_auth_failed` | `test_owner_auth.py::test_non_dev_without_configured_token_fails_closed`, `::test_non_dev_missing_or_wrong_token_is_401_and_audited` |
| 8 | Audit tampering (edit, delete, reorder) | Hash chain + head | verify fails | `test_audit_chain.py` (7 tests, incl. `test_deleted_last_row_is_detected_via_head`) |
| 9 | Unbalanced / invalid draft | Pre-check before queueing | `tool_denied` | `test_tiers_and_approvals.py::test_unbalanced_draft_is_refused_before_queueing` |
| 10 | Runaway automation | T3 daily cap; LLM budget cap | `llm_blocked_budget` | `test_tiers_and_approvals.py::test_t3_actions_are_capped_per_day` |

**Live proof:** `eval/demo_run.py` shows, end to end, a refused transfer request (`money_movement_refused`), approval with a wrong token → 401, approval as actor `agent` → 403, approval by the owner with the token → 200, and a verified audit chain.

## 6. Known risks

| # | Risk | Severity | Mitigation / status |
|---|---|---|---|
| 1 | **Self-declared actor header**: `X-Actor` is recorded but never authorises; one shared owner token, no per-user accounts, no rotation, no rate limiting, no TLS | High for production, low for the demo | Owner actions need the bearer token and fail closed outside dev; run on localhost or behind a reverse proxy; roadmap: per-user auth (OIDC) |
| 2 | **No production authentication / multi-tenant isolation**: company scoping is enforced in code, but read endpoints (chat, reports, audit) need no token; single DuckDB process | High for production | Demo is single-owner; roadmap: authenticated sessions per company, per-tenant databases |
| 3 | **Mock-vs-real LLM gap**: tests and the demo use the deterministic mock provider; a real model may behave differently | Medium | Security does not rely on model behaviour (tiers, approvals, no money tool); real-provider integration checks are manual |
| 4 | **LLM provider data exposure**: with a real provider, redacted text and numbers leave the machine | Medium | Redaction first; amounts and dates stay visible by design; private self-hosted / local-model mode on the roadmap |
| 5 | **Redaction and injection detection are pattern-based**: unusual ID formats or names not in the ledger may pass; novel injection phrasings may not be flagged | Medium | The real control is the bounded blast radius; heuristics are a second layer |
| 6 | **Audit chain is tamper-evident, not tamper-proof**: someone with DB write access can rewrite the whole chain | Medium | Planned: export the head hash to an external location periodically |
| 7 | **Synthetic-data limits**: three synthetic SMEs with answer keys, plus public data for duplicate/forecast checks; real books are messier | Medium | Reported honestly in the evaluation; real-data validation uses public datasets only |
| 8 | **Connector parity gaps**: connector-vs-books parity on some tables is an expected-failure (xfail) test; connectors are sandbox/draft only | Low–medium | Known gap; no connector can write outside a sandbox |
| 9 | **First-call latency**: the first engine call per company computes the full analysis (cold start) | Low | Cached afterwards; UI shows loading states |
| 10 | `unverified_numbers` is a signal, not a block | Low | Flag shown to the owner and logged |
| 11 | `POST /admin/reset` wipes data including the audit chain | Medium if enabled | Disabled by default (`ADMIN_RESET_ENABLED=0`), localhost only, owner token, previous head recorded in the first new audit row |
| 12 | In-memory chat sessions are lost on restart | Low | Acceptable for the demo |
| 13 | Money-movement detection on owner text is a regex | Low | A missed phrasing still cannot move money (no tool exists); only the `money_movement_refused` audit row would be missing |
| 14 | LLM price table may be stale | Low | Check `pricing.json` before relying on spend figures |

## 7. Residual position

In the demo configuration the system cannot move money at all, cannot write to the ledger without the owner's token-authenticated approval, cannot read across companies through the agent, and keeps a verifiable record of everything. It is **not** production-ready for a shared deployment: authentication, tenant isolation, TLS and external audit anchoring are the first roadmap items.
