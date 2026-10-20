# SME Finance Copilot — Master Plan

FinTechathon 2026 (WeBank / Shenzhen), International Track, **Topic E: SME Finance Copilot**.
Theme: "AI as a Financial Participant" — an agent that completes real finance tasks.

## 0. Hard facts

| Item | Value |
|---|---|
| Deadline (registration **and** submission) | **20 Oct 2026, 23:59 Beijing = 21:59 Dhaka = 15:59 UTC** |
| Our internal submit target | **20 Oct, 18:00 Dhaka** (4 h buffer) |
| Code freeze | **20 Oct, 12:00 Dhaka** |
| Scoring | Task completion 40% · Security & compliance 30% · Innovation & interaction 30% |
| Topic E must-haves (named by organiser) | 1) financing-option matching 2) cash-flow management with alerts 3) business financial analysis |
| Language | English everywhere |
| Eligibility | Full-time students at overseas universities only; team 2–5; ≥ 60% non-Chinese; passport + proof of enrolment per member |
| How to register / submit | **By email to fintechathon@ican-x.com** (to be confirmed with the organiser; see `docs/research/OFFICIAL_RULES.md` §8) |
| How judging works | Scripted tasks against a simulated environment, plus attack tests: induced transfers, data extraction, privilege escalation, prompt injection. Online review 22–23 Oct, so the deck must stand on its own. 0–5 bonus for real-world deployment. |
| Licences | Every third-party dataset, library and code must be properly licensed — record it in `docs/LICENSES_DATA.md` |

### Submission checklist (from the official brief)
1. **Presentation deck** — for a final presentation of at most 10 minutes.
2. **Demo video** — at most 5 minutes.
3. **Technical documentation** — system architecture, core algorithm description, security design.
4. **Source code** — GitHub repo link, README, deployment instructions, test cases.
5. **Security self-assessment report** — permission-tier implementation and known-risk list.
6. **Execution evidence** — simulated-account / sandbox operation logs + demo video.

## 0c. Revision 3 (19 Oct) — overrides 0b item 1 and anything else that conflicts

**No private company data is used anywhere in this project.** The real-world pilot (P1) is cancelled. The hackathon build uses only synthetic data (D1) and **public, licensed real datasets** (Kaggle / Hugging Face / UCI / government open data, licence recorded in `docs/LICENSES_DATA.md`).
- "Real data proof" now means running the engine on public real transaction datasets: the forecast backtest on real sales history, plus refund/duplicate/anomaly detection on real ledgers. Results are published with dataset name and licence.
- No real-deployment bonus claim. The copilot is pitched as ready for SMEs, with a private self-hosted mode (already built) for a future pilot after the hackathon.
- Agents never read files outside this repo except public datasets they download into `data/raw/`.

## 0b. Revision 2 (17 Oct night) — partly superseded by 0c

1. ~~Real-world pilot~~ — cancelled, see 0c.
2. **One hero SME in the video: `cn_xborder`** (Shenzhen cross-border seller; WeBank's own market, multi-currency, marketplace fees, financing match with a WeBank-style product). `bd_grocer` and `uk_garden` appear in the scripted-task suite and a 20-second "works for any SME" montage. 
3. **Connectors cut to 6 live.** Generic CSV/Excel with mapping, Google Sheets, QuickBooks Online sandbox, bank statement CSV, wallet statement CSV, and **one live POS API** (Square sandbox; Odoo-in-Docker only if Docker is ready). POS export profiles only where D1 has made a sample file (Square, Odoo, Loyverse). Everything else goes on **one honest roadmap page** (`docs/CONNECTOR_ROADMAP.md`), not fake registry rows. 
4. **Don't oversell ML.** The forecast is explainable statistics with an honest backtest. The pitch is "works with messy, short data", not "needs 5 years". Accuracy claims come from backtests on public real datasets, not from synthetic data we designed.
5. **Proactive, not just reactive ("AI as a financial participant").** A weekly **Owner Brief** is generated without being asked: Cash Truth gap, top 3 alerts, recommended actions with one-click approve/reject for the drafts. It's cheap on top of the engine and shows the agent acting on its own initiative within its permission tier.
6. **Agents merged to save tokens.** Q1 now also does S1's work (one eval + security agent). W1 drafts all deliverables. V1 stays independent. Opus only for M1 and V1; everything else on Sonnet.
7. **Real LLM tested early:** one integration run with the real OpenAI model on the morning of 18 Oct, not at recording time.
8. **Cheap extras kept:** one-click Chinese and Bangla summary of the Owner Brief (for WeBank judges and local owners).
9. **Dropped:** Loyverse live, 30–40-row connector registry, what-if scenarios beyond 3 presets.

## 1. Product in one line

An agent that **connects** to the messy places an SME keeps its numbers, **finds the truth** (books vs. real cash), then **explains, predicts, recommends financing and acts** — only within the permission tier the owner grants, with every step logged.

### The core loop
`Connect → Normalise → Reconcile ("Cash Truth") → Analyse → Predict → Recommend → Act (with approval) → Explain`

### Our differentiator (innovation score)
**Books-vs-Reality reconciliation.** SMEs' software says one thing and the cash drawer, bank and mobile-money wallet say another: unrecorded purchases, cash expenses never entered, receivables that will never be collected, duplicates. The agent computes a **Cash Truth Gap** (book cash vs. verifiable cash), lists the likely causes with evidence rows, and drafts the correcting entries for the owner to approve. Every forecast and financing recommendation is then built on the *corrected* numbers, not the book numbers.

## 2. Scope

### MUST (demo depends on it)
1. **Connector framework + 6 working connectors** (each a plug-in with the same interface, each row keeps its source lineage):
   - Generic CSV/Excel upload with LLM-proposed column mapping and owner confirmation
   - Google Sheets (read)
   - QuickBooks Online **sandbox** (OAuth; read; write *draft* entries only)
   - Bank statement CSV (generic, multi-currency)
   - Mobile-money / wallet statement CSV (e.g. bKash-style, or Wise multi-currency)
   - POS sales export CSV (Square-style)
   - **POS: connection-ready for every relevant POS** (Jawat, 17 Oct): Odoo POS, Klikit/"Clickit", Loyverse, Square, Shopify POS, Toast, Lightspeed, Clover, Zoho, Pospal/Meituan (China), local Bangladeshi POS and others found by research (`docs/research/POS_CONNECTORS.md`). One generic POS adapter + a mapping profile per vendor (fields → canonical `pos_sales_daily`/sales lines), each with a sample export file and a test; 2–3 of them live via real API/sandbox, the rest via their export format.
   - A **connector registry page** lists 30–40 connectors with status *live / sandbox / planned* (honest roadmap, not fake integrations).
2. **Canonical ledger** (DuckDB): accounts, transactions, invoices (AR), bills (AP), inventory movements, payroll, cash counts, FX rates. Every row: `source_system, source_file, source_row_id, ingested_at`.
3. **Cash Truth reconciliation**: match ledger vs. bank / wallet / cash counts; flag unrecorded expenses, unrecorded receipts, stale receivables (aging > 90 d), duplicates, timing differences. Output: gap number, cause list with evidence, draft adjusting entries.
4. **Plain-language reports**: one-page monthly P&L, COGS, gross margin, cash in/out, runway — business numbers only, no jargon (jargon terms get a one-line hover explanation).
5. **Cash-flow forecast + alerts**: 13-week and 12-month forecast with prediction intervals; backtested and the backtest error shown; alerts like "cash falls below 1 month of costs in week 7", "customer X is 45 days late", "supplier bill due".
6. **Financing-option matching**: product catalogue per country (public info, marked *illustrative*), eligibility rules + affordability check (debt-service cover computed from the forecast) + plain explanation of why each product fits or not. Never applies on the owner's behalf.
7. **Agent chat with tool use** that cites the rows behind every number ("from these 14 transactions"). **The LLM never does arithmetic** — all numbers come from deterministic engine functions.
8. **Security by design** (30% of score):
   - Permission tiers: **T0 Read · T1 Analyse/Suggest · T2 Draft (writes go to sandbox/draft, need owner approval) · T3 Limited execute (e.g. reminders) with caps**. **No money movement, ever.**
   - Append-only, hash-chained audit log of every tool call, approval and write.
   - PII redaction before anything goes to the LLM.
   - Prompt-injection guard: ingested file content is data, never instructions (tested with poisoned CSV/PDF rows).
   - Secrets only in `.env`; LLM cost cap.

### SHOULD (if time allows, in this order)
1. What-if scenarios ("raw material +15%", "lose biggest customer").
2. Reminder *drafts* for late payers (email/SMS/WhatsApp text) — drafted, not sent.
3. Report summary in a second language (Bangla / Chinese).
4. Google Sheets write-back of the monthly report.

### WON'T (roadmap slide only)
Live 30–40 connectors, real payments, real loan applications, mobile app, multi-tenant SaaS auth.

## 3. Demo SMEs (proposed by R1 — `docs/research/SME_SELECTION.md`; **approved by Jawat 17 Oct**)

Data window for all three: **2020-01-01 to 2026-09-30**.

| id | Business | Currencies | Entry points | Public anchors |
|---|---|---|---|---|
| `bd_grocer` | Neighbourhood grocery, Mirpur, Dhaka | BDT | bKash-style wallet CSV, bank CSV, Google Sheets notebook | WFP BD food prices, World Bank Pink Sheet, WDI |
| `cn_xborder` | Shenzhen cross-border accessories seller | CNY, USD, EUR, GBP | Excel, marketplace settlement CSV, Wise-style multi-currency CSV | Fed H.10 FX, NY Fed GSCPI, UCI Online Retail II shape |
| `uk_garden` | Garden centre, Yorkshire | GBP | Square-style POS CSV, QuickBooks Online UK sandbox, bank CSV | NOAA GHCN weather, ONS retail index, BoE rate |

Backup if needed: Sri Lanka tea exporter. All flows must be replayable by script (the judges run scripted tasks).

Original constraints:
Constraints given by Jawat:
- **Not all Bangladeshi.** One local (Bangladesh), one from another country, one with international sales / multi-currency.
- **≥ 5 years of history** each, so forecasts and trends are credible.
- Each uses **different entry points** (spreadsheets, accounting software, POS, bank/wallet statements).
- Pick types where integration and the demo are easiest.
- Data: real public data where the licence allows (e.g. commodity prices, FX, public sales datasets), plus seeded, reproducible synthetic accounting data on top, with deliberately injected "data entry problems" so reconciliation has something real to find.


## 4. Architecture

```
frontend/ (Next.js)                backend/ (FastAPI, Python 3.11)
  Chat ─────────────────────────►  api/          REST + SSE streaming
  Dashboard (P&L, cash, runway)    agent/        tool-use loop, prompts, LLM providers
  Cash Truth inbox                   providers/  openai (default), anthropic, mock
  Forecast + alerts                security/     tiers, approvals, audit (hash chain),
  Financing matches                              redaction, injection guard, cost cap
  Approvals queue                  engine/       reconcile, metrics, forecast, financing,
  Audit log viewer                               scenarios, alerts
  Connectors registry              connectors/   base interface + 6 connectors
                                   ledger/       DuckDB canonical schema + lineage
data/synthetic/  (generated, reproducible)   scripts/data/  generators (seeded)
data/raw/        (git-ignored public downloads)    tests/  unit, engine golden numbers, security
```

### LLM runtime
- **OpenAI API is the default** (Jawat has $30 credit). Model name from `.env` (`LLM_PROVIDER`, `LLM_MODEL`), never hard-coded.
- A Claude Pro subscription cannot power the app's API calls, so the Anthropic provider is an optional adapter only.
- **Mock provider** (scripted tool calls) is the default for development and tests, so building costs $0; the real key is used only for integration checks and the demo recording.
- Hard per-request and per-day token/cost cap in config; every call's cost logged.
- Jawat puts the key in `backend/.env` themselves. No agent asks for it, prints it or commits it.

## 5. Timeline (Dhaka time)

| When | Lane A | Lane B |
|---|---|---|
| Fri 17 Oct eve | A1 Core backend scaffold, schema, agent loop, security module | R1 Research: SMEs, datasets, financing products, official rules |
| Sat 18 Oct AM | A1 continues (API, providers, audit) | D1 Data generators for 3 SMEs |
| Sat 18 Oct PM | M1 Engine: reconcile, metrics, forecast, financing | C1 Connectors (6) + registry |
| Sun 19 Oct AM | M1 alerts, scenarios, backtests | F1 Frontend |
| Sun 19 Oct PM | Q1 eval + security; real-public-data validation | F1 continues; integration |
| Mon 20 Oct AM | V1 independent review → fixes; **freeze 12:00** | W1 Tech docs, deck, video script |
| Mon 20 Oct PM | Record demo video (Jawat), sandbox logs export, deploy, README | **Submit by 18:00** |

Two worker agents at a time (Pro plan). The next agent starts when a lane frees up.

## 6. How each feature earns points

| Criterion | What we show |
|---|---|
| Task completion 40% | End-to-end on 3 SMEs: connect → Cash Truth → P&L → forecast + alerts → financing match → approved draft entry in QuickBooks sandbox, with logs |
| Security & compliance 30% | Permission tiers enforced in code, approval gate, hash-chained audit log, redaction, injection tests, known-risk list, no money movement |
| Innovation & interaction 30% | Cash Truth Gap, evidence-cited answers, plain-language reports, what-if, second-language summary |

## 7. Top risks
1. **Time** — 3 days. Mitigation: MUST list only; freeze at 12:00 on the 20th.
2. **Registration not done** — Jawat to email the organiser today and register (students only, passport + enrolment proof per member).
7. **Judges' attack tests** — the security report gets a red-team table covering induced transfers, data extraction, privilege escalation and prompt injection, with a test per row.
3. **LLM credit** — mock provider by default; cost cap.
4. **QuickBooks sandbox OAuth** needs a free Intuit developer account created by Jawat (agents cannot create accounts).
5. **Data licences** — every source logged in `docs/LICENSES_DATA.md`.
6. **Private data** — none used (revision 3).
