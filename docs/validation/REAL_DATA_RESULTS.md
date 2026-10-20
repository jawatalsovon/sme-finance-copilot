# Engine validation on public real data

Owner: M1. Date: 19 Oct 2026.

This page checks two parts of the engine on public data, which no synthetic dataset can show:
- the **forecast**;
- the **duplicate-payment scan**.

It reports only what was measured. None of these datasets is an SME's own accounts, so the full Cash
Truth loop is still validated only on the synthetic SMEs with known, planted problems. This page proves
single components on real data. It does not prove the whole loop.

**How to reproduce.** Run `python -m tests.engine.real_data.validate`. Raw numbers go to
`docs/validation/real_data_results.json`; the code is in `tests/engine/real_data/`. The tests (`pytest -q
tests/engine/real_data`, 4 passed) skip cleanly when `data/raw/` is not downloaded. Dataset choice,
licences and mappings come from R1 (`docs/research/REAL_DATA_VALIDATION.md`).

| Dataset | Licence | What is real |
|---|---|---|
| V1 UCI Online Retail II | CC BY 4.0 | One UK online giftware wholesaler: invoices from Dec 2009 to Dec 2011 |
| V4 Monash Car Parts (Zenodo 4656022) | CC BY 4.0 on the record (the licence of the upstream R package `expsmooth` was not checked) | 2,674 monthly part-sales series, Jan 1998 to Mar 2002 |
| V2 London Borough of Bexley, payments over £500, Aug–Oct 2025 | Open Government Licence | 18,719 payments to 1,187 suppliers by a local council (not an SME) |

## 1. Weekly revenue forecast: UCI Online Retail II (V1)

**Series.** Weekly product revenue: Quantity × Price on non-cancellation invoices, without the non-product
codes (postage, fees, adjustments). It covers the 104 full weeks from Dec 2009 to Nov 2011. We recomputed
it from the original workbook and compared it with R1's derived file: all 104 weeks agree to within
£400, which is under 0.2% of a typical week.

**Method.** The engine's production forecast (`backend/engine/forecast.py::forecast_series`): the average
of seasonal naive, Holt-Winters and Theta, with UK holiday factors. It is compared with R1's baseline,
seasonal naive (the same week last year), using the same protocol.

| Protocol | Engine sMAPE | Seasonal naive sMAPE | Result |
|---|---|---|---|
| Last 13 weeks held out (one origin) | 21.3% | 21.2% | tie (+0.1 points) |
| Last 26 weeks held out (one origin) | 20.4% | 17.7% | engine worse by 2.8 points |
| Rolling 4 weeks ahead, 26 origins in year 2 | **14.5%** | 16.8% | engine better by 2.3 points |

Caveats:
- Only two years exist, so every forecast of the second year's peak learns seasonality from **one**
  earlier Q4. Q4 2010 is 40% of that year's product revenue.
- With under two full seasonal cycles, the engine's Holt-Winters falls back to seasonal naive by design.
  The engine is therefore two-thirds seasonal naive and one-third Theta here.
- Theta alone does badly on this series (51% sMAPE at 13 weeks), and that drags the average down at the
  longer holdouts.
- Single-origin holdouts are one draw each. The rolling result (26 origins) is the more reliable of the three.
- This series is revenue from invoices. It is not a cash-flow series.

## 2. Intermittent demand: Monash Car Parts (V4)

**Sample.** 200 of the 2,509 complete series, drawn with a fixed seed (42). One series had a constant
history and was skipped, leaving **199 evaluated**.

**Method.** The last 12 months are held out. MASE is scaled by the in-sample one-step naive error, the
usual choice for intermittent demand. Lower is better.

| Method | Median MASE | Mean MASE |
|---|---|---|
| Engine (ensemble) | 0.752 | 1.067 |
| Seasonal naive (same month last year) | 0.792 | 1.077 |
| Mean of the last 12 months | 0.792 | 1.001 |
| Naive (last value) | 0.576 | 1.439 |
| Always zero | 0.352 | 0.614 |

The engine beats seasonal naive on **54.3%** of series and naive on 31.2%.

Caveats:
- These are intermittent series: 75% of values are zero. On this kind of data, "always forecast zero"
  scores best on point error, and **the engine is not built for intermittent demand**. A Croston-type
  method would be the right tool and was not added.
- The engine does slightly better than seasonal naive on both the median and the mean.
- Units sold have no prices, so this test says nothing about cash.

## 3. Duplicate-payment scan: Bexley payments over £500 (V2)

**Mapping.** Each payment is a paid bill. One supplier is one normalised supplier name; the same name
can carry several supplier numbers.

**Engine rules** (`backend/engine/duplicates.py`), fixed before scoring. A "schedule" is a supplier and
amount that appears in 2 or more different months, such as rent, a placement or a care package. Schedules
are not flagged as repeats.

| Rule | What it flags |
|---|---|
| `exact_repeat` | Same supplier, same amount, same day, different transaction number, not a schedule |
| `near_repeat` | The same, but 1–7 days apart |
| `amount_typo` | Same supplier, same day, amounts 0.5%–10% apart, neither part of a schedule |
| `reversal` | A credit note equal to an earlier payment to the same supplier within 90 days |
| `schedule_extra` (optional) | A schedule paid more times in a month than its usual number per month |

**Baselines** (R1):
- **R0**: same date, supplier and amount. It reproduces R1's count of 7,221 extra rows exactly.
- **R1**: R0 without schedules.

62% of all payments belong to schedules.

### 3a. Untouched ledger: candidates for a human audit

| Detector | Flagged pairs | Per 1,000 payments | Breakdown |
|---|---|---|---|
| R0 | 7,221 | 385.8 | — |
| R1 | 1,419 | 75.8 | — |
| Engine, core rules | 2,394 | 127.9 | 1,340 exact repeats, 880 amount typos, 174 near repeats |
| Engine + schedule rule | 4,707 | 251.5 | adds 2,313 schedule extras |

**The data has no duplicate labels, so none of these counts is a count of errors.** The 50 largest
core-rule candidates are listed in `real_data_results.json` (`v2.audit_top50`) for a human audit:
- suppliers are pseudonymised (`SUP-xxxxxx`);
- each candidate has its expense type, dates, amounts and source rows;
- 37 are amount typos, 10 exact repeats and 3 near repeats.

Reading the list, many amount-typo candidates fall on bulk funding days (for example, early-years funding
paid to many providers on 26 Aug). That is a known source of false alarms: different invoices from one
provider on one day. **No audit has been done yet, so no precision on natural candidates is claimed.**

### 3b. Real ledger with 350 INJECTED errors (seed 42)

The injected errors are labelled as such everywhere. They are copies of randomly drawn real rows:

| Kind | Count | How it was made |
|---|---|---|
| Exact repeats | 150 | New transaction number, same day |
| Near repeats | 100 | Same row, 1–7 days later |
| Amount typos | 50 | Same day, amount off by 1–10% |
| Credit-note reversals | 50 | Negative copy, 1–30 days later |

Recall by kind and in total:

| Detector | Exact repeats | Near repeats | Amount typos | Reversals | Total recall | Flagged pairs |
|---|---|---|---|---|---|---|
| R0 | 150/150 | 0/100 | 0/50 | 0/50 | 0.429 | 7,371 |
| R1 | 69/150 | 0/100 | 0/50 | 0/50 | 0.197 | 1,488 |
| Engine, core rules | 69/150 | 36/100 | 34/50 | 47/50 | **0.531** | 2,570 |
| Engine + schedule rule | 122/150 | 77/100 | 34/50 | 47/50 | **0.800** | 4,966 |

Recall split by what the injected row copied:

| Detector | Copies of one-off payments (150) | Copies of schedule payments (200) |
|---|---|---|
| Engine, core rules | 0.967 | 0.205 |
| Engine + schedule rule | 0.967 | 0.675 |

Caveats:
- **Precision.** Strict precision, which counts every flag that does not touch an injected row as false,
  is 7.2% (core) and 5.7% (with the schedule rule). The real precision is unknown: the untouched ledger
  already produces the natural candidates in 3a, and their truth is unknown.
- **Flag rate.** False-alarm load is better read as flags per 1,000 payments on the untouched ledger (3a).
  The engine's core rules raise about 1.7 times R1's load and catch 2.7 times R1's injected errors.
- **Schedules.** The schedule rule trades many more candidates for recall on repeats of regular
  payments. Which setting to use is a choice for the person reviewing.
- **Not like an SME.** A council ledger with many identical regular payments behaves differently from an
  SME's books. Results may not transfer.
- **Bank side.** No bank statement exists for this ledger, so the statement-matching part of Cash Truth is
  not tested here.

## What we do not claim

- No claim of accuracy for Cash Truth as a whole on real data. That is tested on the synthetic SMEs only
  (`tests/engine/report.py`).
- No precision for natural duplicate candidates until the top-50 list has been audited by a person.
- No claim that the forecast "beats" a baseline in general. On V1 it is better on rolling 4-week-ahead
  forecasts, equal at 13 weeks and worse at 26 weeks. On V4 it is slightly better than seasonal naive and
  worse than naive on the median.
