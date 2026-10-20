# Deck outline and speaker notes

`deck.pptx`, 11 slides, about 10 minutes (roughly 55 seconds per slide). Built by `eval/build_deck.py`. Fill in team and presenter names on slides 1 and 11.

## 1. SME Finance Copilot

- The cash you can prove, not just the cash in the books
- Draft-only AI for small businesses. The owner approves, always.
- FinTechathon 2026 | International Track | Topic E
- [Team name] | [Presenter names]

**Speaker notes:** Hello, we built the SME Finance Copilot. In ten minutes: the problem, our key insight, how the agent works, a demo, the security design, honest evaluation numbers, and where we go next. Everything shown uses synthetic or public data.

## 2. The problem: small businesses fly blind

- Books live in sheets, apps and heads; bank, wallet and marketplace data live elsewhere
- Fees never booked, bills entered twice, receipts in the wrong account, stale FX rates
- Owners decide on cash, hiring and loans from numbers that are wrong
- A generic AI chat makes it worse: it can invent numbers and be talked into acting

**Speaker notes:** An SME owner usually has several systems that never agree, and nobody reconciles them line by line. If you hand this to a general chatbot, two things go wrong: it can make up figures, and it can be manipulated into doing something harmful with money.

## 3. Insight: books versus reality

- Books say what was recorded. Statements and settlements say what happened.
- The gap between them is the most valuable number for an owner
- Hero case, a Shenzhen cross-border seller (synthetic): the books show about CNY 676k more cash than can be proven
- Every part of the gap is explained, line by line, with evidence

**Speaker notes:** Our insight is simple: compare the books with independent evidence. On our hero synthetic business the books overstate cash by about 676 thousand yuan. The product explains where every part of that gap comes from. The number comes from our deterministic engine, not from the language model.

## 4. Solution: an agent that analyses, not acts

- A deterministic engine computes every number; the LLM only chooses tools and words
- Tiers: T0 read | T1 analyse | T2 draft with owner approval | T3 capped low-risk
- No money-moving tool exists. Corrections are drafts the owner approves
- Answers cite evidence; unsupported numbers are flagged

**Speaker notes:** The loop: the owner asks, the agent picks a tool, code checks tier, company and arguments, the engine returns numbers with evidence, the agent explains. Anything that writes is a draft in an approval queue. There is simply no tool that pays, transfers or applies for credit.

## 5. Demo: Owner Brief and Cash Truth

- Weekly brief: gap, alerts, KPIs, pending approvals.  Cash Truth: book vs verified by account, findings by cause with confidence.
- Screenshots: brief.png, cash-truth.png

**Speaker notes:** Left, the Owner Brief: the first thing the owner sees. Right, Cash Truth: each account's book against verified balance, then findings grouped by cause, each with evidence rows and a balanced draft correction.

## 6. Demo: chat with evidence, approvals and audit

- Chat answers cite evidence and a transfer request is refused.  Drafts wait for the owner; every step is in a verifiable audit log.
- Screenshots: chat-answer.png, approvals.png

**Speaker notes:** Left, the chat: numbers link to source rows. Ask it to transfer money and it refuses and logs it. Right, the approval queue: the agent queues a draft, only the owner can approve with a token, and the audit screen verifies the hash chain.

## 7. Financing matching

- Public financing products with machine-checkable eligibility rules
- Facts computed from the data; what the books cannot show is marked to-confirm
- Affordability: loan sized to the forecast shortfall; DSCR of 1.25 or more = affordable
- Illustrative only. The system never applies.
- Screenshots: financing.png

**Speaker notes:** For businesses with a seasonal cash dip we match public products and show rule by rule why each passes or fails, plus affordability using a conservative rate. It never applies for anything.

## 8. Security by design

- Model not trusted: tiers enforced in code, outside the model
- Owner-only approval with token; payload hashed and re-verified; the agent cannot approve
- Hash-chained audit log, verifiable at /audit/verify
- PII redacted before the LLM; injection flagged and enclosed; company scoping; cost cap
- 65 security tests: induced transfer, data extraction, privilege escalation, prompt injection

**Speaker notes:** The four attack classes the judges test are each covered by a control and a test. The key idea: even a fooled model has nothing dangerous to call. Our sandbox run shows a refused transfer, a wrong-token approval refused, the owner approving, and the audit chain verifying.

## 9. Evaluation: honest numbers

- Synthetic SMEs with exact answer keys (bd / cn / uk):
- Recall on detectable problems 0.98 / 0.98 / 1.00; precision 0.98 / 0.99 / 0.99
- Overall recall 0.84 / 0.87 / 0.83 (inventory shrinkage is invisible to any source)
- Gap equals the answer key to the cent; books-basis P&L matches to the cent
- Real public data: duplicate-payment recall 0.80 vs 0.43 for the standard rule (350 injected errors in 18,719 real UK council payments)
- Forecast: roughly level with naive baselines. We do not oversell it.

**Speaker notes:** On synthetic data we know the truth, so we can measure. Recall on detectable problems is near 1. The lower overall recall is the shrinkage we cannot see by design. On real public payments data our duplicate detector finds 80 percent against 43 percent for the standard rule. And we say clearly: the forecast is about as good as naive baselines, not better.

## 10. Architecture

- Sources -> loaders with lineage -> DuckDB ledger -> deterministic engine
- Tool registry + executor (tiers) -> agent loop <-> LLM (mock by default, redacted input)
- Approval queue -> drafts / connector sandbox -> hash-chained audit
- FastAPI backend | Next.js owner app | about 240 tests

**Speaker notes:** Everything between the sources and the owner is deterministic and auditable, except the language model, which sits inside the agent loop, receives redacted data, and cannot act on its own.

## 11. Roadmap and ask

- Now: sandbox connectors (QuickBooks, files, Sheets, POS), 3 demo SMEs
- Next: more connectors and bank feeds, per-user auth and tenant isolation, OCR for paper documents
- Private self-hosted mode with a local model: no data leaves the business
- Team: [names, universities]  |  Ask: feedback and a pilot partner
- Code, docs and evidence are in the repository

**Speaker notes:** Next we deepen connectors and add production authentication, then a self-hosted private mode where nothing leaves the business. We would welcome feedback and a pilot partner. Thank you.
