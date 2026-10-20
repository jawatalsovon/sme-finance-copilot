# Synthetic SME data (D1)

**All data here is synthetic.** No real firm, person, bank, wallet or marketplace record is used. Names are fictional; vendor file layouts are
"style of" (bKash-style, Wise-style, Amazon-style, Square-style, QuickBooks-importable, Odoo/Loyverse-shaped), not copies. Public series
(`data/anchors/`, see `data/anchors/SOURCES.md`) only shape prices, FX, weather, freight and seasonality.

Regenerate: `python scripts/data/generate.py --seed 42` (default seed 42, about 1 minute, byte-identical for the same seed; hashes in `MANIFEST.sha256`).
Check: `python scripts/data/validate.py`. Load the demo state into a DB: `scripts.data.seed.seed(db)` (books view, about 15 s).
Data window for all three: **2020-01-01 to 2026-09-30**.

## 1. Design

| | `bd_grocer` Mirpur Family Store | `cn_xborder` Lanxin Digital Accessories | `uk_garden` Hollinbank Garden Centre |
|---|---|---|---|
| Base currency | BDT | CNY (sells USD, GBP, EUR) | GBP |
| Revenue/yr | 25M (2020, COVID) to 36M (2025) BDT | 7.5M to 13.9M CNY (USD 1.05M to 1.88M) | GBP 1.0M to 1.5M |
| Gross margin | about 11-12% | about 40% after freight, duties, goods | about 40-42% |
| Net margin | 2-3.5% | 3-8% (2022 freight squeeze, 2025 tariffs) | 3.5-6% |
| Systems | Google-Sheets notebook, bKash-style wallet CSV, bank CSV, physical cash | Excel books, Amazon-style settlement CSVs, Wise-style statements, CNY bank CSV | Square-style POS CSVs, QuickBooks-importable CSVs, bank CSV |
| Anchors used | Eid/Ramadan dates, WFP + Pink Sheet cost index (COGS ratio), Dhaka rain, modelled BDT/USD | Fed H.10 FX (daily), NY Fed GSCPI (freight), Pink Sheet Brent (goods cost), Q4/Prime Day/CNY | Bradford weather (warm dry weekends, wet days), ONS CPI (cost ratio), Easter/Mother's Day/bank holidays, BoE rate (in anchors) |
| Shocks | COVID lockdown Mar-May 2020, 2022 price squeeze, Jul-Aug 2024 curfew | 2020-21 boom, 2021-22 freight spike, Black Friday/Prime Day, 2025 tariff step (assumed) | closures 2020-21, 2020 surge, 2022-23 energy spike |

Financing signal in `cn_xborder`: factory deposits (30%) and balances (70%) are paid 3-4 months before Q4 cash arrives; a working-capital loan
(draw 1 Sep, repay 25 Aug) and an annual dividend are visible in the CNY account. `uk_garden`: Jan-Mar stock build, quarterly VAT, a 2021 term loan.
`bd_grocer`: pre-Eid stock build, wholesaler advances (dadon), 2023 equipment loan.

Assumptions (not facts): Eid/CNY/Prime Day dates are hand-set (the `holidays` package is not installed); 2025 tariff effects; all business sizes;
BDT/USD is interpolated from WDI annual averages (flagged modelled); series ending before Sep 2026 (weather, CPI, WFP) are filled with climatology / last value.
Order-level POS exists only from 2026-04-01 for `uk_garden` (to keep the size down); earlier days are daily Square-style summaries (`pos_daily_imports`).
Not planted (documented gaps): VAT mis-coding on mixed baskets, in-transit stock counted as available, rounding of Square gift-card edge cases.

## 2. Layout

```
data/synthetic/<company>/
  answer_key.json          every planted problem, honest noise, true P&L, true cash, Cash Truth gap
  sources/                 what the SME's systems export (vendor formats, sloppy where noted)
  books/                   canonical tables (backend/ledger/schema.sql columns) = what the books say
  truth/                   canonical tables = what really happened (+ external_statement_lines = bank/wallet/cash reality, pos_* for uk_garden)
                           + source_trace.csv.gz  (file,row_id,ref_kind,ref_id: every source row -> truth/book entry id)
data/synthetic/MANIFEST.sha256
```
Big tables are `.csv.gz` (pandas reads them directly). Money is decimal text in major units. Account ids are `<company>-<code>`.
Truth entry ids `je-<company>-NNNNNN`; the same entry in the books is `bk-<company>-NNNNNN`; book-only additions are `bk-<company>-A00001`.

## 3. Source files and how C1 should map them

**bd_grocer** (`sources/`)
- `notebook_<year>.xlsx` (Google-Sheets style): month tabs `Jan26` ... columns `Date | Details | Head | Mode | In | Out | Note`; one row = one 2-line journal entry
  (`notebook_mapping.json`: Mode = cash/bank/bKash/Udhar(AP)/Baki(AR)/Dadon/Stock side, Head = other account, In = Mode account debited).
  Sloppy on purpose: dates as real dates, `dd/mm/yyyy`, `5-Sep`, ambiguous `mm/dd/yyyy`, `dd.mm.yy`; amounts as numbers, `12,500`, `Tk 12500`, Bangla digits, `12500/-`;
  head typos; mode case; blank rows; about 3% rows entered late at the bottom. Tab `Baki` = household credit/payment rows (`system: gsheets`).
- `bkash_style_wallet_statement_<year>.csv`: `TrxID,Date(dd/mm/yyyy),Time,Transaction Type,Counterparty,Reference,Credit,Debit,Fee,Balance`. Credit is gross; Fee is deducted from the balance. -> `external_statement_lines` channel `wallet`.
- `mirpur_commercial_bank_statement_<year>.csv`: `Txn Date(dd-Mon-yyyy),Value Date,Narration,Chq/Ref No,Withdrawal,Deposit,Balance` -> channel `bank`.
- `cash_counts.csv`: month-end physical count of till and safe -> channel `cash_count`.

**cn_xborder**
- `lanxin_books_<year>.xlsx`: sheets `GL_Journal` (voucher lines, multi-currency, `Rate to CNY`), `Sales_by_Channel`, `PO_Tracker`, `Supplier_Payments`, `AR_Wholesale`. Books = GL_Journal.
- `amazon_style_settlement_<US|UK|EU>_<year>.csv`, `storefront_payout_report_<year>.csv`: Amazon V2-style columns; each settlement = one summary row (settlement-id, dates, deposit-date, total-amount, currency)
  followed by daily-aggregated detail rows (Principal, Commission, FBAPerUnitFulfillmentFee, RefundCommission, Cost of Advertising, Storage Fee, Subscription Fee, REVERSAL_REIMBURSEMENT, reserve rows).
  The last settlement is open (blank total). Sum of detail rows = total = the deposit in the Wise statement.
- `wise_style_statement_<USD|EUR|GBP>.csv`: Wise column set; conversions carry Exchange From/To/Rate/Total fees.
- `cny_bank_statement_<year>.csv`, `petty_cash_counts.csv`.
- `truth/settlement_index.json` and `answer_key.json -> marketplace_settlements`: one row per settlement.

**uk_garden**
- `square_daily_sales_summary.csv` (2020-01..2026-03, daily totals -> `pos_daily_imports`), `square_transactions_2026.csv` + `square_items_2026.csv` (2026-04..09 order level -> `pos_orders/lines/payments`), `square_deposits.csv` (payouts, gross/fees/net).
- `qbo_chart_of_accounts.csv`, `qbo_customers.csv`, `qbo_suppliers.csv`, `qbo_invoices.csv`, `qbo_bills.csv`, `qbo_journal_entries.csv` (QuickBooks Online import layouts; the books).
- `bank_current_account_<year>.csv`: `Date(dd/mm/yyyy),Description,Money In,Money Out,Balance`; `till_cash_counts.csv`.
- POS profile samples (only Square, Odoo, Loyverse): `odoo_pos_order_lines_sample_2026-09.csv`, `loyverse_receipts_sample_2026-09.csv` (same September 2026 orders as the Square files).

## 4. Answer key (for Q1)

`answer_key.json` keys: `summary`, `problems`, `honest_noise`, `true_monthly_pnl`, `book_monthly_pnl`, `true_month_end_cash`, `cash_truth_gap` (+ `marketplace_settlements` for cn).
- **Problem types** (extras beyond the brief are marked +): `unrecorded_cash_expense`, `unrecorded_receipt`, `phantom_receivable`, `stale_receivable` (finding only: invoices > 90 days overdue at each as-of date; no ledger change),
  `duplicate_entry`, `timing_difference`, `misclassified_expense`, `fx_misbooking`, `missing_marketplace_fee`, `inventory_shrinkage`,
  + `unrecorded_fee` (wallet/bank/Square fees), + `misrouted_receipt` (wallet payment typed as cash), + `personal_spend_mixed`, + `gift_card_misbooked`.
- Each problem: `id, type, subtype, date, amount, currency, amount_base, description, truth_entries, book_entries, affected_source_rows` (file + row; role `book_row_to_correct` or `evidence_row`),
  `gap_effect_native` (per cash account, book minus truth), `net_profit_effect_if_uncorrected_by_month`, `fix`, `details`.
- **Fix** = void the listed `book_entries`, then add the listed `add_entries` (full lines with account ids). After all fixes the books equal the truth line for line (validate.py proves it).
- **Cash Truth gap** (`cash_truth_gap[as_of]`): `gap` = book cash minus verified cash (bank/wallet statement balances and physical counts), in base currency at the as-of rate; per account; `gap_pct_of_ttm_revenue`;
  `gap_by_problem_type`; `unexplained` is 0 (every unit of gap is a planted problem). As-of dates: every quarter end plus 2025-12-31 and 2026-09-30.
- **Honest noise** (`honest_noise`): looks suspicious but is correct (owner top-ups, large equipment/loan deposits, two real identical purchases, round deposits, reserve holds recorded properly,
  Wise spread, freight spikes, Square payout in transit). A detector that flags these has a false positive.
- Scoring suggestion: detection recall/precision per `type` against `problems` (match on truth entry ids or date+amount within 1%), gap error = |reported - key| / key, P&L figures exact to the unit vs `true_monthly_pnl`.

## 5. Planted-problem summary (seed 42)

Printed by `generate.py`; see `answer_key.json -> summary`. Counts by type, revenue per year and the 2026-09-30 / 2025-12-31 gaps are listed in the D1 report and regenerate identically.

## 6. Loading

`scripts/data/seed.py: seed(db) -> counts` loads `books/*` (and reality tables `external_statement_lines`, `pos_*` from `truth/`) for all three companies with `if_exists="skip"`; it never touches `pos_sales_daily`.
Test: `tests/data/test_seed.py`.

## 7. Licences and attribution

Anchor licences match `docs/LICENSES_DATA.md` (no differences). Ship with the deck:
World Bank Commodity Price Data and WDI (CC BY 4.0); WFP Bangladesh food prices via HDX (CC BY-IGO); Federal Reserve H.10 via FRED (public domain, citation requested);
NY Fed Global Supply Chain Pressure Index (not endorsed by the New York Fed); UCI Online Retail II (CC BY 4.0, shape statistics only); NOAA GHCN-Daily (Menne et al. 2012, doi:10.1175/JTECH-D-11-00103.1, DOI 10.7289/V5D21VHZ);
ONS Retail Sales Index and CPI, Bank of England Bank Rate (contain public sector information licensed under the Open Government Licence v3.0). Dependencies added by D1: `openpyxl` (MIT), `xlrd` (BSD).
