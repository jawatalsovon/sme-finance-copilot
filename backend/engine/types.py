"""Result types returned by the finance engine (contract between M1, A1's agent tools and F1).

Rules for every type here
  * Money is Decimal in Python and serialises to a JSON number (float) for the API.
  * Every figure carries `evidence`: a list of evidence refs "table:id" (see
    backend/ledger/lineage.py) for the source rows it was computed from. If a figure is
    built from thousands of rows, list the most relevant ones (cap ~200) and set
    `evidence_count` to the full number.
  * `summary` fields are plain-language sentences produced BY CODE with numbers already
    formatted (backend.core.util.format_money). The LLM may quote them; it never computes.
  * Amounts are in the company's base currency unless a field says otherwise.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

Amount = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]
OptAmount = Annotated[
    Optional[Decimal], PlainSerializer(lambda v: None if v is None else float(v), return_type=Optional[float], when_used="json")
]
EvidenceRef = str  # "table:id"


class EngineModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Figure(EngineModel):
    """One number with its provenance."""
    key: str                                  # machine key, e.g. "book_cash"
    label: str                                # plain words, e.g. "Cash according to your books"
    value: Amount
    currency: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    evidence_count: int = 0                   # total source rows (may exceed len(evidence))
    formula: str = ""                         # how it was computed, in words
    hint: str = ""                            # one-line jargon explanation for the UI hover


# ---------------------------------------------------------------- Cash Truth
class DraftEntryLine(EngineModel):
    """debit/credit are in the line's own `currency`; `amount_base` = (debit - credit) x fx_rate in the
    company's base currency, signed (debit +). The lines of an entry balance on amount_base (sum = 0);
    never add debit/credit of lines in different currencies."""
    account_id: str
    debit: Amount = Decimal("0")
    credit: Amount = Decimal("0")
    currency: str
    fx_rate: Amount = Decimal("1")
    amount_base: OptAmount = None
    counterparty_id: Optional[str] = None
    memo: str = ""


class DraftJournalEntry(EngineModel):
    """A proposed correcting entry. Never written by the engine - the agent's T2 tool
    `draft_journal_entry` queues it for owner approval, then writes it with status 'draft'.
    Balanced in base currency: total_debit_base == total_credit_base (sum of lines' amount_base = 0)."""
    entry_date: dt.date
    memo: str
    lines: list[DraftEntryLine]
    currency_base: Optional[str] = None
    total_debit_base: OptAmount = None
    total_credit_base: OptAmount = None
    reason: str                               # why this entry is proposed (plain words)
    evidence: list[EvidenceRef] = Field(default_factory=list)


FindingKind = Literal[
    "unrecorded_expense",       # money left bank/wallet/cash but no book entry
    "unrecorded_receipt",       # money came in but no book entry
    "stale_receivable",         # AR older than the threshold, likely uncollectable
    "duplicate_entry",          # same transaction booked twice
    "timing_difference",        # booked and banked in different periods (explains, no fix)
    "fx_difference",            # currency conversion mismatch
    "fee_not_recorded",         # wallet / POS / bank fees
    "cash_count_shortfall",     # counted cash below book cash
    "other",
]


class Finding(EngineModel):
    id: str                                   # stable within the result, e.g. "f-001"
    kind: FindingKind
    title: str                                # "3 bank charges not in your books"
    explanation: str                          # plain-language detail, numbers pre-formatted
    amount: Amount                            # impact on the gap, base currency, signed
    currency: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    proposed_entries: list[DraftJournalEntry] = Field(default_factory=list)


class ChannelBalance(EngineModel):
    channel: Literal["bank", "wallet", "cash_count", "pos_settlement"]
    account_id: Optional[str] = None
    label: str
    book_balance: Amount
    verified_balance: Amount
    difference: Amount                        # verified - book
    currency: str
    evidence: list[EvidenceRef] = Field(default_factory=list)


class CashTruthResult(EngineModel):
    company_id: str
    as_of: dt.date
    currency: str
    book_cash: Figure                         # cash + bank + wallet per the books
    verified_cash: Figure                     # per statements / counts
    gap: Figure                               # verified - book
    explained: Figure                         # part of the gap covered by findings
    unexplained: Figure
    channels: list[ChannelBalance] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    proposed_entries: list[DraftJournalEntry] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------- P&L / KPIs
class PnLLine(EngineModel):
    key: str                                  # revenue | cogs | gross_profit | opex:<subtype> | ...
    label: str
    amount: Amount
    evidence: list[EvidenceRef] = Field(default_factory=list)
    evidence_count: int = 0
    hint: str = ""


class MonthlyPnL(EngineModel):
    company_id: str
    month: str                                # "YYYY-MM"
    currency: str
    basis: Literal["books", "corrected"] = "books"   # corrected = after Cash Truth drafts
    revenue: Figure
    cogs: Figure
    gross_profit: Figure
    gross_margin_pct: Optional[float] = None
    operating_expenses: Figure
    operating_profit: Figure
    net_profit: Figure
    cash_in: Figure
    cash_out: Figure
    lines: list[PnLLine] = Field(default_factory=list)
    comparison_prev_month: Optional[dict[str, float]] = None   # key -> % change
    notes: list[str] = Field(default_factory=list)
    summary: str = ""


class KPI(EngineModel):
    key: str                                  # runway_months, dso_days, gross_margin_pct, ...
    label: str
    value: Optional[float] = None
    unit: Literal["currency", "pct", "days", "months", "ratio", "count"]
    currency: Optional[str] = None
    period: str = ""                          # e.g. "2026-09" or "last 90 days"
    status: Literal["good", "watch", "bad", "unknown"] = "unknown"
    evidence: list[EvidenceRef] = Field(default_factory=list)
    explanation: str = ""
    hint: str = ""


class KPISet(EngineModel):
    company_id: str
    as_of: dt.date
    currency: str
    kpis: list[KPI]
    summary: str = ""


# ---------------------------------------------------------------- forecast
class ForecastPoint(EngineModel):
    period_start: dt.date
    period_end: dt.date
    inflow: Amount
    outflow: Amount
    net: Amount
    closing_cash: Amount                      # point forecast
    closing_cash_lower: Amount                # lower bound of the interval (interval_level, 80%)
    closing_cash_upper: Amount
    closing_cash_lower_95: OptAmount = None   # 95% band
    closing_cash_upper_95: OptAmount = None


class BacktestResult(EngineModel):
    method: str
    metric: Literal["MAPE", "sMAPE", "MAE", "RMSE", "WAPE"]
    value: float
    horizon: int
    folds: int
    interval_coverage: Optional[float] = None  # share of actuals inside the interval
    explanation: str = ""


class CashflowForecast(EngineModel):
    company_id: str
    as_of: dt.date
    horizon: int                              # number of periods
    granularity: Literal["week", "month"]
    currency: str
    basis: Literal["books", "corrected"] = "corrected"
    opening_cash: Figure
    interval_level: float = 0.8
    points: list[ForecastPoint]
    backtest: Optional[BacktestResult] = None
    method: str = ""
    assumptions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------- alerts
class Alert(EngineModel):
    id: str
    severity: Literal["info", "warning", "critical"]
    kind: Literal["low_cash", "late_customer", "bill_due", "unusual_expense", "cash_truth_gap",
                  "margin_drop", "concentration", "other"]
    title: str
    message: str                              # plain language, numbers pre-formatted
    due_date: Optional[dt.date] = None
    amount: OptAmount = None
    currency: Optional[str] = None
    counterparty_id: Optional[str] = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    suggested_action: str = ""


class AlertList(EngineModel):
    company_id: str
    as_of: dt.date
    alerts: list[Alert]
    summary: str = ""


# ---------------------------------------------------------------- financing
class FinancingProduct(EngineModel):
    id: str
    provider: str
    name: str
    country: str
    product_type: Literal["term_loan", "working_capital", "overdraft", "invoice_financing",
                          "trade_finance", "microfinance", "govt_scheme", "lease", "other"]
    currency: str
    min_amount: OptAmount = None
    max_amount: OptAmount = None
    apr_min_pct: Optional[float] = None
    apr_max_pct: Optional[float] = None
    tenor_months_min: Optional[int] = None
    tenor_months_max: Optional[int] = None
    requirements: list[str] = Field(default_factory=list)
    source_url: str = ""
    illustrative: bool = True                 # public info, not an offer


class EligibilityCheck(EngineModel):
    rule: str
    passed: bool
    reason: str
    evidence: list[EvidenceRef] = Field(default_factory=list)


class Affordability(EngineModel):
    requested_amount: Amount
    monthly_repayment: Amount
    dscr: Optional[float] = None              # debt-service cover ratio from the forecast
    min_forecast_cash_after: OptAmount = None
    affordable: bool
    explanation: str


class FinancingMatch(EngineModel):
    product: FinancingProduct
    eligible: bool
    fit_score: float = Field(ge=0, le=1)
    checks: list[EligibilityCheck] = Field(default_factory=list)
    affordability: Optional[Affordability] = None
    explanation: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)


class FinancingMatches(EngineModel):
    company_id: str
    as_of: dt.date
    currency: str
    matches: list[FinancingMatch]
    disclaimer: str = ("Illustrative public information only. Not financial advice and not an offer. "
                       "The copilot never applies for credit on your behalf.")
    summary: str = ""


# ---------------------------------------------------------------- scenarios
class ScenarioChange(EngineModel):
    kind: Literal["revenue_pct", "cogs_pct", "opex_pct", "price_pct", "lose_customer",
                  "new_loan", "delay_receivables_days", "one_off_expense", "fx_pct"]
    value: float                              # % for *_pct, days, or amount
    target: Optional[str] = None              # counterparty_id / account_id when relevant
    start: Optional[dt.date] = None
    description: str = ""


class ScenarioResult(EngineModel):
    company_id: str
    changes: list[ScenarioChange]
    baseline: CashflowForecast
    scenario: CashflowForecast
    deltas: list[Figure] = Field(default_factory=list)
    explanation: str = ""
    summary: str = ""


# ---------------------------------------------------------------- owner brief (weekly, unprompted)
class BriefAction(EngineModel):
    id: str                                   # "act-1"
    kind: Literal["approve_draft", "send_reminder", "review_finding", "plan_payment", "consider_financing",
                  "watch_cash"]
    title: str
    why: str                                  # plain words, numbers pre-formatted
    amount: OptAmount = None
    currency: Optional[str] = None
    finding_id: Optional[str] = None          # Cash Truth finding the action comes from
    alert_id: Optional[str] = None
    draft: Optional[DraftJournalEntry] = None # the entry to queue for approval (T2), never written by the engine
    approval_id: Optional[str] = None         # filled by the agent once the draft is queued for approval
    product_id: Optional[str] = None          # financing product, when kind = consider_financing
    evidence: list[EvidenceRef] = Field(default_factory=list)


class OwnerBrief(EngineModel):
    company_id: str
    as_of: dt.date
    week_start: dt.date
    week_end: dt.date
    currency: str
    headline: str
    cash_truth_gap: Figure
    verified_cash: Figure
    gap_pct_of_revenue: Optional[float] = None
    forecast_end_cash: OptAmount = None       # point forecast after 13 weeks
    forecast_lowest_cash: OptAmount = None
    forecast_lowest_week: Optional[dt.date] = None
    top_alerts: list[Alert] = Field(default_factory=list)       # at most 3
    actions: list[BriefAction] = Field(default_factory=list)
    kpis: list[KPI] = Field(default_factory=list)
    summary: str = ""                         # template text, no LLM (English)
    summaries: dict[str, str] = Field(default_factory=dict)   # {"en", "zh", "bn"}: same facts, fixed templates
