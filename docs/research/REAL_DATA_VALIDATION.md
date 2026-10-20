# Real public data for validating the engine (R1, 2026-10-19)

Context: plan revision 3 cancelled the private pilot. Our "real data proof" now comes from **public, properly licensed real datasets**. This file says which ones, what each can and cannot prove, how to map them to `backend/ledger/SCHEMA.md`, and which numbers to report. Facts I measured are marked **[measured]** with the command or file; everything else is a recommendation.

## 1. Verdict

| # | Dataset | Licence | Real what? | Validates | Status |
|---|---|---|---|---|---|
| V1 | **UCI Online Retail II** (already in `data/raw/uci_online_retail2/`) | CC BY 4.0 | One UK online giftware wholesaler, 1 Dec 2009 – 9 Dec 2011, 1,067,371 invoice lines, 53,628 invoices, 43 countries | Weekly revenue forecast backtest (2 years only); refund/cancellation linking; natural duplicate and adjustment lines | **Ready** |
| V2 | **London Borough of Bexley, payments over £500** (3 months in `data/raw/bexley_spend_over_500/`) | Open Government Licence | A real public body's payments ledger: 18,719 payments, 1,190 suppliers, Aug–Oct 2025 | Duplicate-payment detection (semi-synthetic injection plus an audit of natural candidates); not an SME | **Ready** |
| V3 | **Iowa Liquor Sales** (yearly files, 2020–2024) | CC BY 4.0 | Retailer **purchases** (not consumer sales) per store and day, 5 years | A 5-year, store-level backtest on real weekly data | **Blocked on a large download** (see §5) |
| V4 | Monash **Car Parts** (in `data/raw/monash_zenodo/`) | CC BY 4.0 (Zenodo); upstream R package licence unchecked | 2,674 real intermittent monthly series, Jan 1998 – Mar 2002 (51 months) | Intermittent-demand forecasting at item level; a backup for V3 | Ready, secondary |
| — | Monash Dominick (weekly SKU profit) | CC BY 4.0 on Zenodo, **upstream terms unread** | 115,704 weekly series, no dates | Nothing judge-facing until the upstream terms are checked | Held back |

Two honest limits, to say out loud in the deck: **none of these is an SME's own accounts.** V1 is a wholesaler's invoice stream without payments or bank data; V2 is a public body's payment list; V3 is purchase history. Each proves one engine component on real data, not the whole Cash Truth loop. The whole loop stays on synthetic data with injected, known problems.

## 2. V1: UCI Online Retail II

- Page and licence: <https://archive.ics.uci.edu/dataset/502/online+retail+ii> (CC BY 4.0, read earlier this session; DS07 in `docs/LICENSES_DATA.md`). Direct file: `https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip` (already downloaded by D1: `data/raw/uci_online_retail2/online_retail_II.xlsx`, 45,622,278 bytes; it is over the 20 MB subset limit but already on disk and git-ignored).
- Columns (Online Retail II): Invoice, StockCode, Description, Quantity, InvoiceDate, Price, Customer ID, Country.

**What I measured** (script run with `python -I` in the repo venv; outputs in `data/raw/uci_online_retail2/`):

| Fact | Value |
|---|---|
| Sheets / rows | 2 sheets (2009-10: 525,461; 2010-11: 541,910) = 1,067,371 lines [measured] |
| Date range | 2009-12-01 07:45 to 2011-12-09 12:50 [measured] |
| Invoices | 53,628, of which 8,292 are cancellation invoices (number starts with "C"), 19,494 cancellation lines [measured] |
| Missing customer id | 243,007 lines (22.8%) [measured] |
| Negative quantity without a "C" invoice | 3,457 lines (stock adjustments, damaged goods and similar) [measured] |
| Exact duplicate lines (all columns equal, beyond the first) | 34,335 [measured] |
| Non-product codes | POST 2,122, DOT 1,446, M (manual) 1,421, BANK CHARGES 102, AMAZONFEE 43, D (discount) 177, S 104, CRUK 16, ADJUST 67, B ("Adjust bad debt") 6 lines worth about −£147,614 [measured] |
| Weekly revenue series | 106 calendar weeks; 104 full weeks after dropping the first and last partial weeks [measured] |

### 2.1 Mapping to the canonical schema

The data has no payment, due-date, bank or tender information, so it maps to the **POS/order tables** and not to AR or bank reconciliation.

| Source | Canonical |
|---|---|
| One company, e.g. `uk_giftware_public`, country GB, base currency GBP, industry `wholesale_giftware` | `companies` |
| Customer ID (numeric, already pseudonymous), Country | `counterparties` (kind `customer`); one shared `anonymous` counterparty for the 243,007 lines with no id |
| Each Invoice | `pos_orders`: `external_order_id` = Invoice, `channel` = `online`, `status` = `sale`; a "C" invoice gets `status` = `refund` and `refund_for_order_id` = the original order if the matching rule below finds exactly one, else NULL; amounts positive magnitudes; `business_date` = InvoiceDate in Europe/London |
| Each line | `pos_order_lines`: sku = StockCode, name = Description, qty = abs(Quantity), `unit_price` = Price, `line_total` = qty × price. **Whether prices include VAT is not stated**: record "assumed ex-VAT" in the profile |
| POST, DOT | order-level `service_charge` (shipping) |
| D | order-level `discount` |
| BANK CHARGES, AMAZONFEE, CRUK | not sales: load as expense `journal_entries` (fees) so the "unrecorded fees" rules have real examples |
| B ("Adjust bad debt"), ADJUST, ADJUST2, S, M, TEST001, TEST002 | flag and exclude from revenue; B as a bad-debt `journal_entries` row |
| Repeated identical lines | **keep** both rows; flag, never drop (SCHEMA.md: repeated ids are a data problem to flag) |

`pos_sales_daily` then gives the weekly revenue series for the forecast. No `pos_payments` rows exist, so payout-matching cannot be validated here.

### 2.2 What to report

**(a) Forecast backtest on weekly product revenue.** Series: weekly sum of Quantity × Price for product lines on non-cancellation invoices, excluding the non-product codes above; 104 full weeks.

- Protocol: rolling origin, horizons 4 and 13 weeks, origins from week 53 onward (so a 52-week seasonal lag exists), so the evaluation period is the second year only.
- Metrics: sMAPE and MASE (scale = in-sample seasonal-naive error), and the **coverage of the 80% prediction interval** (target 80%).
- Baselines to beat: seasonal naive (lag 52), naive last value, mean of the last 8 weeks.
- Baseline numbers [measured, same script, `data/raw/uci_online_retail2/series_baselines.json`]: last 13 weeks held out (ending 4 Dec 2011): seasonal naive sMAPE **21.1%**, mean of last 8 weeks 49.0%, naive last value 59.7%; last 26 weeks held out: seasonal naive **17.7%**, mean of last 8 weeks 36.6%, naive last value 57.6%; rolling 4-week-ahead over 26 origins: seasonal naive **16.8%**, mean of last 4 weeks 19.4%. Q4 2010 (Oct–Dec) is 40.3% of that year's product revenue, which is why the seasonal baseline wins. Total cancellations are −£1.53 million against £20.8 million of sales lines [measured]. The exact-duplicate lines carry about £431,700 of extra value [measured].
- Limits to state: only two years exist, so the Q4 peak in 2010 is predicted from exactly one earlier Q4; any model "learns seasonality" from a single cycle. A forecast that cannot beat seasonal naive here is not evidence against it, and one that does is encouraging, not proof.

**(b) Refund/cancellation linking.** Rule-based baseline R0: a cancellation line is linked to an earlier sale line with the same customer and StockCode and a sold quantity at least the cancelled quantity. [measured]: of 18,744 cancellation lines with a customer id, R0 links 16,467 (87.9%); requiring the same unit price too links 15,619 (83.3%).
- Report: the engine's link rate at the same strictness, the share of links that are **unambiguous** (exactly one candidate original), and the precision on a random sample of 100 links reviewed by hand (no ground truth exists in the data, so this is the only way to claim correctness).
- Do not claim "100% matched": the file does not say which sale a cancellation reverses.

**(c) Natural anomalies for Cash Truth-style detection.** Run the engine's duplicate and anomaly rules over the lines and report counts per rule, and for each rule the precision of a random sample of 50 flagged lines checked by hand. The data does not label which repeats are real double entries (two identical lines on one invoice can be legitimate), so the honest claim is "N candidates, X% confirmed on a 50-line audit".

## 3. V2: London Borough of Bexley payments over £500

- Source page and licence: <https://www.bexley.gov.uk/bexley-business-employment/business-services/contracts-tenders-and-procurement/expenditure-records>. The council states the data is published under the Open Government Licence; values are net of VAT, and a minus sign marks a supplier credit note (read on that page).
- Files used (exact URLs, saved in `data/raw/bexley_spend_over_500/`): `https://www.bexley.gov.uk/sites/default/files/2025-10/Aug_2025_v1.csv`, `.../2025-11/sept-2025.csv`, `.../2025-12/october-2025.csv` (737,041 + 755,097 + 689,137 bytes). Row 1 is a title line; row 2 is the header: Payment Date, Transaction Number, Non Recoverable VAT, Amount, Supplier, Supplier Number, Expense Type, Directorate, Service Area.
- More months are on the same page (monthly files back to 2019, quarterly before that). Take more only if the engine needs a longer window.

**What I measured** [measured: `python -I` with pandas over the three files]:

| Fact | Value |
|---|---|
| Rows | 18,719 (6,549 / 6,414 / 5,756 per month), 1,190 suppliers, dates 1 Aug – 31 Oct 2025 |
| Duplicate transaction numbers | 0 |
| Negative amounts (credit notes) | 0 in these three months |
| Baseline rule R0 "same date + supplier + amount" | 1,934 groups, 9,155 rows, 7,221 "extra" rows |
| Of those groups, supplier + amount recur in at least 2 of the 3 months (a payment schedule) | 1,579 (**81.6%**) |
| Left as non-recurring candidates | 355 groups, extra amount about £4.95 million |
| Same supplier + amount within 7 days on a different date | 1,162 pairs |

Reading: the naive rule R0 flags about 5.5 times more groups than a recurrence-aware rule leaves, and most R0 hits are plainly regular payments (housing placements, care packages). That is exactly the evidence that a smarter detector is needed. It is not evidence about true duplicates, because **the data carries no duplicate labels**.

### 3.1 Mapping

| Source | Canonical |
|---|---|
| The council as one company `bexley_public`, GB, GBP, industry `local_government` | `companies`. Say clearly that it is not an SME |
| Supplier, Supplier Number | `counterparties` kind `supplier`, `external_ref` = Supplier Number. **Pseudonymise names in any public output** (some suppliers may be sole traders or individuals) |
| Each payment row | `bills` (`bill_no` = Transaction Number, `issue_date` = Payment Date, `total` = Amount, status `paid`, `expense_account_id` from Expense Type) plus one `bill_payments` row (same date and amount) |
| Expense Type | `accounts` (type `expense`, subtype `opex` unless obviously `rent` or `utilities`) |
| Non Recoverable VAT | `bills.tax` |
| Journal | optional: one `journal_entries` row per payment, debit expense, credit a single bank account |
| Bank side | none exists. **Do not fabricate one and call it real.** A bank side can be generated from these rows only for the semi-synthetic test below, labelled as such |

### 3.2 What to report (three numbers, each with its baseline)

1. **Natural candidates and audit.** Run the engine's duplicate detector on the three months. Report candidate groups and the precision on the top 50 (by amount) reviewed by hand against the supplier and expense type, with the reviewer's rule written down before looking. Compare with R0 and with R1 = R0 minus recurring schedules (the numbers above).
2. **Semi-synthetic recall with a fixed seed.** Inject known problems into the real rows, record them in an answer key, then score. Suggested injection: 150 exact repeats (new transaction number, same date, supplier, amount), 100 near repeats (same supplier and amount, date shifted 1–7 days), 50 amount typos (±10%, same supplier and date), and 50 credit-note reversals. Report recall and precision per kind for the engine, R0 and R1. This is the only way to give recall on real background data, and it must be labelled "real ledger with injected errors".
3. **False-positive rate on the untouched ledger**: flags per 1,000 payments, for the engine, R0 and R1.

## 4. V4: Monash Car Parts (secondary)

- Record: <https://zenodo.org/records/4656022> (DOI 10.5281/zenodo.4656022), licence CC BY 4.0 on the record; the series come from the R `expsmooth` package (the licence of that upstream data was not checked). File `car_parts_dataset_with_missing_values.zip`, 39,656 bytes, saved in `data/raw/monash_zenodo/`.
- [measured] 2,674 monthly series, all 51 months long (1998-01 to 2002-03), 4.5% of values missing, 74.8% of the non-missing values are zero (intermittent). Total units per month fall from 1,789 to 935 over the period; 592 series have sales in at least 20 months.
- Mapping: parts as `inventory_items`, monthly units as `inventory_movements` of kind `sale` (negative quantity), no prices or cash values exist, so it cannot feed revenue. Use only to test item-level or intermittent forecasting.
- Metrics: MASE and RMSSE per series against seasonal naive and a 12-month mean, over the last 12 months; report the median and the share of series where the engine beats seasonal naive; use a Croston-style method as the extra baseline for intermittent series.

## 5. V3: Iowa Liquor Sales (the best candidate, not yet pulled)

- **What it is.** Each row is a purchase by an Iowa liquor licensee (grocery, liquor and convenience stores) from the state, by product and date, at store level. It is the store's **purchasing history**, a proxy for cost of goods and replenishment, **not what customers paid**. Say that on every chart.
- **Licence.** CC BY 4.0, stated on each record, publisher Alcohol Operations Bureau, Iowa Department of Revenue. 2020: <https://catalog.data.gov/dataset/iowa-liquor-sales-2020> (dataset 1257); 2021: dataset 1258; 2022: 1259; 2023: 1260; 2024: 1261 (IDs read from the catalog.data.gov pages). CSV: `https://idh-be.iowa.gov/api/v1/datasets/<ID>/rows.csv` (served as a zip, `iowa_liquor_sales_2020_1257_rows.zip`).
- **Why it is not pulled.** The Iowa Data Hub moved off the Socrata API the task assumed (`data.iowa.gov/resource/...` now returns 404). The new hub offers **no filter or limit parameter**: when I asked a `rows.json` endpoint for 2 rows, it started streaming the whole year, and I stopped it after about 621 MB. A single year is therefore a download of several hundred MB, which breaks the 20 MB subset rule. I deleted that partial file and did not retry.
- **What is ready.** `docs/research/scripts/iowa_store_weekly.py` streams the five yearly files, decompresses on the fly, never stores the full file, and writes one small CSV of per-store weekly purchases (about 15–30 MB for all stores and five years; filter to a few stores afterwards). It is self-tested on a tiny zip (`--selftest` passes) but **not run against the live files, and the live column names are unconfirmed** (the script matches them case-insensitively and fails loudly if they differ). Command:

```bash
python -I docs/research/scripts/iowa_store_weekly.py data/raw/iowa_liquor/iowa_store_weekly_2020_2024.csv
```

  Needs only the standard library and roughly 1–3 GB of transfer in total (my estimate from the partial JSON; unconfirmed). **Please approve before it runs**, from the orchestrator or Jawat; I did not start it.
- **Mapping once pulled.** One company per chosen store (country US, USD, industry `liquor_retail`); each invoice → `bills` (supplier = a single "State of Iowa" counterparty) and `inventory_movements` kind `purchase` (bottles); weekly dollars → the forecast series.
- **Choosing stores.** Do not hand-pick. Take stores with at least 200 non-zero weeks of the 260, draw 30 with a fixed seed, and report the **median** over the 30 plus the single store used in the demo chart.
- **Metrics.** Weekly purchase dollars, 5 years (260 weeks): rolling-origin backtest with horizons 4 and 13 weeks, origins from week 105; sMAPE, MASE and 80% interval coverage; baselines seasonal naive (lag 52), naive, mean of last 8 weeks. Report the share of stores where the engine beats seasonal naive. With five years, the seasonal baseline is meaningful, unlike V1.

## 6. For Jawat to download (login needed, not downloaded)

I could not open Kaggle (the browser tool refused the site), so licences below are **unverified** and must be read on each page.

| Item | URL | File | Why | Licence note |
|---|---|---|---|---|
| Iowa Liquor Sales (Kaggle copy) | <https://www.kaggle.com/datasets/residentmario/iowa-liquor-sales> | `Iowa_Liquor_Sales.csv` (large) | A fallback for V3 only if the Iowa Data Hub download is refused; contents may stop before 2020 | unverified |
| Brazilian E-Commerce Public Dataset by Olist | <https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce> | `olist_orders_dataset.csv`, `olist_order_payments_dataset.csv`, `olist_order_items_dataset.csv` | Real marketplace orders with payments and refunds, 2016–2018 | Believed **non-commercial** (CC BY-NC-SA 4.0); flag before use; many sellers, not one SME |

Competition datasets (Rossmann Store Sales, M5, Store Item Demand Forecasting, Corporación Favorita) stay **rejected**: competition rules, not open licences (`DATASETS.md` §5).

Not needed: UCI Online Retail II is already local and free of login.

## 7. Datasets considered and flagged

- **Dominick (Monash version):** 115,704 weekly series, no dates, CC BY 4.0 on Zenodo but the original Kilts Center terms are unread. Held back.
- **Dunnhumby "Complete Journey", Instacart:** my understanding is that they carry non-commercial or academic-only terms; not checked; do not use.
- **Other UK council spend files:** same OGL pattern as Bexley, useful if a second ledger is wanted (Wigan publishes payments over £500 in CSV with a field description file; not read in detail).

## 8. Results table to show judges (template)

| Test | Data | Metric | Baseline | Engine | Notes |
|---|---|---|---|---|---|
| Weekly revenue forecast | V1, 104 weeks | sMAPE, MASE, 80% coverage | seasonal naive (see §2.2) | fill in | Two years only |
| Refund linking | V1, 18,744 cancellation lines | link rate, unambiguous share, audited precision | rule R0: 87.9% | fill in | No ground truth |
| Duplicate detection, real ledger with injected errors | V2 + 350 injected, fixed seed | recall, precision per kind | R0 and R1 | fill in | Label "injected" |
| Duplicate candidates, untouched | V2, 18,719 rows | candidates, audited precision@50 | R0: 1,934 groups; R1: 355 | fill in | No labels in data |
| 5-year store backtest | V3 (when pulled) | median sMAPE, MASE | seasonal naive | fill in | Purchases, not sales |

## 9. Open points

1. V3 needs an approval for a large download (§5), or the team drops it and relies on V1 plus V4.
2. V1 has two years only; do not claim a three-year backtest from it.
3. The data in V1 and V2 comes from third parties; the attribution lines are in `docs/LICENSES_DATA.md` (DS07, DS13).
4. Bexley supplier names may include individuals: pseudonymise before any screenshot or LLM call.
