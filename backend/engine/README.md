# Finance engine: core algorithms (owner: M1)

The engine is the copilot's deterministic financial brain. Every number the agent shows is computed here,
never by the language model. The same data and the same question always give the same answer, and every
figure carries evidence references (`table:id`) to the source rows it came from. The engine only reads:
corrections come back as **draft journal entries**. They are written only after the owner approves them
(tier T2, executed by the agent's tools). No money ever moves.

| Function | Plain-words purpose | Module |
|---|---|---|
| `cash_truth(company, as_of)` | Books vs. verifiable cash: the gap, its causes and the correcting drafts | `cash_truth.py`, `matching.py` |
| `monthly_pnl(company, month, basis)` | One-page P&L on the books basis or the corrected basis | `pnl.py` |
| `kpis(company, as_of)` | Runway, margins, days to get paid / to pay, growth, concentration, aging | `pnl.py` |
| `cashflow_forecast(company, horizon, granularity)` | 13 weeks or 12 months ahead, with 80% / 95% ranges and a backtest | `forecast.py`, `calendars.py` |
| `alerts(company, as_of)` | What needs action now, ranked by severity | `alerts.py` |
| `financing_matches(company)` | Which public financing products fit, and whether the business can afford them | `financing.py` |
| `scenario(company, changes)` | Three what-if presets re-run through the forecast | `scenario.py` |
| `owner_brief(company, as_of)` | The weekly brief the copilot sends unprompted | `brief.py` |

## 1. Cash Truth: books vs. reality

**Question answered:** "What is my true cash position, and why does it differ from my books?"

1. **Balances.** For every cash, bank and wallet account we compare two figures:
   - *book cash*: the sum of posted journal lines;
   - *verified cash*: the balance on the latest bank or wallet statement line, or, for tills and
     safes, the latest physical cash count plus the book movements after it.

   Foreign-currency accounts are valued at the as-of exchange rate.
   **Gap = verified cash − book cash.** A negative gap means the books show more cash than can be proven.
2. **Line-by-line matching** of each bank and wallet statement against the book entries on that account
   (`matching.py`). The passes run from strictest to loosest, and each pass only uses what is still unmatched:
   1. *exact*: same day and same amount, one to one;
   2. *window*: same amount, dates up to 7 days apart, closest first. A match with different dates is a
      **timing difference**;
   3. *day net / day by direction*: all open items of a day add up to each other. This handles daily
      totals in the books against many small statement lines, such as wallet sales;
   4. *residual*: a day's totals differ by exactly one or two statement lines. Those lines are the exceptions;
   5. *near*: same direction, dates up to 3 days apart, amounts within 15%. The amounts differ: fees
      taken off a payout, or a different exchange rate;
   6. *same day*: one open item on each side on the same day. A larger difference is still the same
      transaction, for example a loan repayment that also took interest.

   Before matching, the engine detects whether a statement's *fee* column is already inside the amount
   (Wise style) or charged on top of it (bKash style). It picks the reading under which more lines match exactly.
3. **Classifying the exceptions** (each finding has an amount, a confidence, a plain explanation, the evidence rows and a balanced draft):
   - money out that the books do not show:
     - fees and charges → *fee not recorded*;
     - payments to a known supplier (fuzzy name match) → *unrecorded expense*;
     - payments to unknown or private recipients, grouped by month → *unrecorded expense*, with owner's drawings proposed;
   - money in that the books do not show → *unrecorded receipt*. When customers have open invoices it is
     proposed against receivables; otherwise it is proposed as other income;
   - receipts that the books put in the wrong cash account on the same day, for example wallet sales typed in as cash;
   - book entries the statement cannot confirm. When a matched twin exists (same amount, similar description,
     within 31 days), the entry is a *duplicate*;
   - amount differences: currency conversions credited at a different rate (*FX difference*), payouts
     smaller than booked (*fees not recorded*), loan repayments larger than booked (*interest not recorded*);
   - fees reported per transaction (wallets) are compared, month by month, with the fee entries in the books.
4. **Marketplace settlements** (one-to-many, net of fees). Each settlement report is split into buckets:
   sales, refunds, fees, reimbursements, reserve and payout. Each bucket is compared with the book entries
   on the marketplace clearing account for the same period. Fees deducted but never booked, and
   reimbursements never booked, become findings. A payout smaller than booked is explained by the
   settlement's fees or reserve, so it is reported once, not twice. A card processor's deposit report
   (gross, fee, net per payout) is used the same way.
5. **Cash counts.** A till has no statement. Between two counts we compare how much the counted cash changed
   with how much the books say it changed. First we remove what other findings already explain:
   - receipts booked to the till that really went to the wallet;
   - goods paid twice;
   - bank deposits booked on another day.

   A shortfall that remains is reported as *cash spent but not recorded*.
6. **Checks on the books alone:**
   - goods received against an advance and paid again in cash;
   - the same supplier bill entered twice (same amount and document number within days);
   - entries booked to an account other than the one their description usually goes to;
   - supplier credit notes booked as income;
   - book revenue above the till system's net sales (for example gift cards sold);
   - foreign-currency costs booked at a frozen, out-of-date exchange rate;
   - receivables with no payment for 180 days (likely uncollectable: write-off draft) or more than 90 days overdue.

**Explained vs. unexplained.** The cash effect of every finding that is still open at the as-of date is
added up; whatever remains is reported as unexplained. Nothing is forced to balance: if the books and
reality disagree for a reason we cannot see, it stays visible.

**How we measure it** (synthetic SMEs from D1, with answer keys used only in tests, never in the engine):
recall, precision and gap error per SME (see `tests/engine/report.py`).

## 2. P&L and KPIs

Account subtypes drive the buckets:
- revenue;
- cost of goods sold;
- running costs (all other expenses except interest and tax);
- other income;
- net profit = gross profit + other income − all non-COGS expenses.

*Money in / money out* counts receipts into and payments out of cash, bank and wallet accounts, excluding
transfers between the business's own accounts. The **corrected basis** adds every Cash Truth draft entry as
if the owner had approved it. Nothing is written.

KPIs:

| KPI | How it is computed |
|---|---|
| Runway | Verified cash ÷ average monthly money out of the last 3 months |
| Gross margin and net margin | Over the last 12 months |
| DSO, DPO, DIO and cash conversion cycle | Receivables, payables and stock ÷ 12-month revenue or cost of goods × 365 |
| Revenue growth | Last 12 months vs. the 12 months before |
| Largest customer's share | Largest customer's invoiced sales ÷ 12-month revenue |
| Receivable aging | Buckets by days overdue |
| Payable aging | Buckets by days overdue |
| Cash Truth gap | As % of revenue |

## 3. Cash-flow forecast

- **Series.** Weekly or monthly money in and money out on the corrected basis, forecast separately.
  Closing cash = opening *verified* cash + cumulative (in − out).
- **Holidays that move.** Eid al-Fitr and Eid al-Adha for Bangladesh, Chinese New Year, 11.11, Black Friday
  and the Christmas peak for China, UK bank holidays, Easter and the spring planting season. Each one's
  effect is estimated as a multiplicative factor: the median of actual ÷ the local baseline in the periods it
  covers. The factor is removed from the history before modelling and put back on the future periods that
  contain the holiday.
- **Models** (equal-weight average, all explainable):
  - *seasonal naive*: same period last year, scaled to the recent level;
  - *Holt-Winters*: level, damped trend and yearly season, with smoothing weights picked by a small grid search;
  - *Theta*: on the de-seasonalised series, a smoothed level plus half of the straight-line trend.

  We use the average because no single model wins everywhere. On the demo SMEs, the cumulative sMAPE of
  the ensemble vs. the best single model was:

  | SME | 13 weeks: ensemble | 13 weeks: best single | 12 months: ensemble | 12 months: best single |
  |---|---|---|---|---|
  | bd_grocer | 10.5% | 10.8% | 6.0% | 4.9% |
  | cn_xborder | 23.3% | 18.5% | 7.8% | 9.0% |
  | uk_garden | 11.5% | 11.6% | 4.5% | 3.3% |

  LightGBM was not added. It is not installed, and with about 80 months of history per SME there is
  too little data for it to be expected to beat these models in the backtest.
- **Backtest.** Rolling origin: we forecast from up to 24 past dates and compare with what really happened.
  The result shows the sMAPE of money in and money out for each model and for the ensemble.
- **Ranges.** The 80% and 95% bands are the empirical quantiles of the backtest's cumulative errors at each
  horizon, so they widen honestly the further out we look. The backtest also reports interval coverage.

## 4. Alerts

| Alert | When it fires | Severity |
|---|---|---|
| Low cash | The 13-week forecast falls below one month of costs | Warning |
| Low cash | The 13-week forecast falls below zero | Critical |
| Low cash | Only the cautious end of the 80% range falls below zero | Info |
| Late customer | More than 30 days past due | Warning |
| Late customer | More than 90 days past due | Critical |
| Bill due | A large supplier bill is overdue or due within 14 days | — |
| Margin drop | Last month's gross margin is 3 or more points below the 12-month average | — |
| Unusual expense | An account's spend last month is above its 12-month mean + 3 standard deviations | — |
| Cash Truth gap | The gap is at least 0.5% of yearly revenue | Warning |
| Cash Truth gap | The gap is at least 2% of yearly revenue | Critical |
| Customer concentration | One customer is 30% or more of sales | — |

Every alert carries evidence rows and a suggested action.

## 5. Financing matches (illustrative, never applies)

The catalogue (`docs/research/financing_catalogue.json`, from R1) has machine-checkable eligibility rules.
Facts are computed from the data:
- years trading;
- 12-month revenue;
- export revenue in USD;
- invoiced (B2B) share;
- months of marketplace and card history;
- insolvency, from net assets;
- need for working capital (the forecast dips below one month of costs).

Facts the books cannot show (credit-bureau status, owner age, tax grade) are listed as *to confirm*. They
never count as passed.

**Affordability.**
- *Loan size*: the forecast shortfall, clipped to the product's limits.
- *Rate*: the highest rate quoted in public sources, a conservative assumption.
- *Repayment*: an annuity over the product's tenor.
- *DSCR*: cash available for debt service ÷ (existing + new loan payments). Cash available for debt
  service is operating cash flow without loans and owner money, interest added back, over the last
  12 months, scaled by the forecast.

A DSCR of 1.25 or more is "affordable". The repayment is also shown as a share of monthly free cash flow.
Products are ranked by rules passed, affordability, cost and fit with the need.

## 6. Scenarios (three presets)

| Preset | What changes |
|---|---|
| Input cost +15% (`cogs_pct`) | Money out + (cost-of-goods share of money out) × 15% |
| Lose the biggest customer (`lose_customer`) | Money in − that customer's share of the last 12 months |
| FX move ±10% (`fx_pct`) | Foreign-currency receipts × (1 ± 10%) |

The changed flows go through the same forecast. The result shows the baseline and the scenario side by
side, and the change in closing cash, the lowest point and the week cash would go below zero.

## 7. Owner Brief

Built from templates, not from the LLM. It contains:
- the Cash Truth gap and its biggest causes;
- the 13-week cash outlook;
- the top 3 alerts;
- up to 5 recommended actions.

Each correction action carries its draft entry. The agent queues the draft for approval and records the
approval id; it is approved or rejected with one click.
