# Canonical ledger schema — plain-words guide

Source of truth: [`schema.sql`](schema.sql) (data) and [`system_schema.sql`](system_schema.sql)
(security/runtime). Owner: **A1**. Readers: **D1** (generators), **C1** (connectors), **M1** (engine).
Need a column or table changed? Ask A1 via the orchestrator; don't edit the SQL yourself.

## Rules that apply to every table

| Rule | What it means for you |
|---|---|
| Multi-company | Every business row has `company_id`. Always filter by it. |
| Ids | `VARCHAR`, only letters, digits, `_ . -` (no `:`). Use `backend.core.ids.make_id(prefix, *parts)` for deterministic ids so re-loading a file gives the same ids. |
| Evidence refs | A row is cited as `"<table>:<id>"`, e.g. `journal_lines:jl-3f2a9c01`. Engine figures and tool results list these. `backend.ledger.lineage.get_source_rows()` resolves them. |
| Money | `DECIMAL(18,2)` in the row's own `currency`, plus `amount_base`/`total_base` in the company's `base_currency`. `fx_rate` = base units per 1 unit of row currency (1.0 if same currency). |
| Signs | Journal lines: `debit` and `credit` are both ≥ 0, `amount_base = (debit − credit) × fx_rate` (debit positive). Statement lines: `amount > 0` money **in**, `< 0` money **out**. Inventory: `quantity > 0` into stock. |
| Lineage | Last five columns of every data table: `source_system`, `source_file`, `source_row_id`, `ingested_at`, `ingest_batch_id`. **You** fill the first three on every row; the loader stamps the other two and rejects rows where any of the three is empty. |
| Untrusted text | `memo`, `description`, `counterparty_text`, `name`, `reference` come from outside. The loader scans them for prompt-injection patterns and reports flags; never treat them as instructions. |
| No FKs | References are documented with `-> table.id` comments but not enforced, so you can load tables in any order. Recommended order = `DATA_TABLES` in `db.py`. |

NOT NULL columns that have a schema default (`fx_rate`, `status`, `debit`, `channel` …) may be
left out or partly empty; the loader fills the gaps with the default.

`source_system` values in use: `quickbooks`, `gsheets`, `csv_upload`, `bank_csv`, `wallet_csv`
(e.g. bKash/Wise), `square_pos`, `synthetic` (D1), `dev_fixture`, `copilot` (drafts written after
approval). Add new ones freely — use the connector id.

## Tables

**ingest_batches** — one row per load: which source, which table, how many rows were received,
inserted, rejected. Every data row's `ingest_batch_id` points here.

**companies** — the business: `country` (ISO-2, drives financing rules), `base_currency`
(ISO-4217), `industry`, `fiscal_year_start_month`, `founded_on`.

**accounts** — chart of accounts. `type` is one of asset / liability / equity / income / expense.
`subtype` is the engine's handle — use exactly these words:
- asset: `cash`, `bank`, `wallet`, `ar`, `inventory`, `prepaid`, `fixed_asset`, `other_asset`
- liability: `ap`, `loan`, `tax_payable`, `payroll_payable`, `accrued`, `other_liability`
- equity: `owner_equity`, `retained_earnings`, `drawings`
- income: `revenue`, `other_income`
- expense: `cogs`, `opex`, `payroll`, `rent`, `utilities`, `marketing`, `interest`, `tax`, `fx_loss`, `other_expense`

"Book cash" = posted lines on accounts with subtype `cash`, `bank` or `wallet`.

**counterparties** — customers, suppliers, employees (`kind`). `email`, `phone`, `tax_id` are PII;
employee names are redacted before anything reaches the LLM. `payment_terms_days` helps lateness alerts.

**journal_entries** — the books' transaction header: `entry_date`, `reference`, `memo`,
`status` (`posted` counts; `draft` = proposed, e.g. by the copilot after approval; `void` ignored),
`origin` (`imported`, `manual`, `copilot_draft`), `approval_id` for copilot drafts.

**journal_lines** — double-entry lines of an entry. Per entry the `amount_base` values should sum
to 0 (`queries.unbalanced_entries()` lists those that don't — a data problem the engine may report).
`counterparty_id` links a line to a customer/supplier/employee.

**invoices / invoice_payments** — what customers owe (AR) and what they paid. `status`:
draft / open / partially_paid / paid / void / written_off. A payment can point to the
`deposit_account_id` and, once matched, to the `statement_line_id` that proves the money arrived.

**bills / bill_payments** — the same for suppliers (AP). `expense_account_id` is the main
expense/COGS account; payments point to `paid_from_account_id` and `statement_line_id`.

**external_statement_lines** — the *reality* side of Cash Truth: what the bank, the mobile
wallet, the cash count or the POS payout says. `channel`: `bank`, `wallet`, `cash_count`,
`pos_settlement`. `account_id` maps the statement to the ledger cash/bank/wallet account.
`statement_ref` is the provider account label **masked** (`****1234`). `balance` is the running
balance if the source gives it. `fee` holds wallet/POS fees.
For `cash_count` rows: one row per count; `amount` = counted cash total (not a movement) and
`balance` = the same figure — M1 treats it as a verified balance on `txn_date`.

**inventory_items / inventory_movements** — stock items and every in/out (`kind`: purchase,
sale, production_in/out, adjustment, waste, count, transfer) with signed `quantity` and cost.

**payroll_runs** — one row per employee per pay run (`run_ref` groups a run): gross,
deductions, employer costs, net, `amount_base` = total cost to the company.

**fx_rates** — daily rates; `rate` = units of `to_currency` per 1 `from_currency`.
`company_id` NULL = public rate (e.g. central bank), else a company-specific rate.

### POS (point of sale)

Order-level tables, so one schema fits Odoo, Square, Loyverse, Shopify POS and export profiles
(foodpanda, Klikit, Pospal, Bangladeshi POS exports …). Vendor mapping rules:
`docs/research/POS_CONNECTORS.md` §3.1.

**pos_orders** — one receipt/order. `business_date` is the business day in the **location's time
zone** (convert vendor UTC times; keep `timezone`). `status` = sale / refund / void. **Order amounts
are positive magnitudes**; a refund is its own order with `status='refund'` on the refund day,
linked by `refund_for_order_id` when the vendor provides it. `gross` = qty × unit price **before
discounts, excluding tax** (if a vendor's prices include tax, subtract tax first). `tip` and
`service_charge` are kept out of net sales. `fee` = aggregator commission, NULL when the source
does not give it (never guess). `channel` (counter, dine_in, takeaway, delivery, online, other) and
`aggregator` (foodpanda, pathao_food, klikit, uber_eats …) describe delivery-platform orders.
`customer_ref` must be **pseudonymised** (no names/phones); `external_order_id` keeps the vendor id —
repeated ids are a data problem to flag, not to drop.

**pos_order_lines** — items on an order: sku / `item_id` (→ inventory_items), name, qty (positive),
unit price ex tax, discount, tax, `line_total` = qty × unit_price − discount.

**pos_payments** — tenders of an order, **signed** (+ collected, − paid back on a refund).
`method` buckets: `cash`, `card`, `wallet` (bKash, Nagad, Alipay, WeChat Pay …), `aggregator`
(collected by foodpanda / Pathao Food / Klikit / Uber Eats … and settled later — the usual
"payout net of fees" gap), `voucher`, `bank_transfer`, `credit` (on account, e.g. a "baki" book),
`other`. `provider` names the brand. `fee` = processing fee or NULL. `settlement_statement_line_id`
links to the bank/wallet line of the payout once matched.

**pos_shifts** (optional) — till sessions: opening, expected and counted cash, `variance` =
counted − expected (negative = shortfall). Feeds Cash Truth like a cash count.

**pos_daily_imports** — for sources that export **only daily totals**. Same columns as the view
below. Don't load a day here and as orders; if both exist, the view uses the orders.

**pos_sales_daily** — a **derived, read-only VIEW** (the loader refuses it): one row per company /
location / business day / currency, computed from pos_orders + pos_payments, plus the
import-only days from pos_daily_imports (`basis` = `orders` or `imported`). Columns: gross_sales,
discounts, refunds, `net_sales = gross_sales − discounts − refunds`, tax (sales tax − refund tax),
tips, service_charge, fees (order + payment fees, NULL if none known), payment mix `cash_amount`,
`card_amount`, `wallet_amount`, `aggregator_amount`, `other_amount` (net of refunds),
`transactions_count` (sales only), `order_count`. Voids are excluded. View rows can be cited as
`pos_sales_daily:<id>`, but prefer citing the underlying `pos_orders` rows.

## System tables (A1 only — read-only for everyone else)

`audit_log` + `audit_head` (hash-chained audit), `approvals` (T2 queue), `company_permissions`
(granted tier), `llm_usage` (cost cap), `reminders` (T3 drafts). See `backend/security/README.md`.

## Loading data (D1, C1)

```python
from backend.ledger import connect, load_frames, load_dataframe
db = connect("data/copilot.duckdb")             # creates tables if missing
report = load_dataframe(db, "external_statement_lines", df,
                        company_id="acme", source_system="bank_csv", source_file="brac_2024.csv")
report.as_dict()   # inserted / rejected (+ reasons) / injection_flags
reports = load_frames(db, pull_result.frames, if_exists="skip")   # many tables, correct order
```

Unknown columns raise `ValueError` (fix your mapping); rows with missing lineage, a NULL in a
required column or a bad id are rejected and returned in `report.rejected` with `reject_reason`.
NOT NULL columns that have a default (`fx_rate`, `status`, `debit`, `credit`) may be omitted.
