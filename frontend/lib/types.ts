// Subset of backend/engine/types.py and API payloads that the UI reads.
export type Tier = "T0" | "T1" | "T2" | "T3";

export interface Company {
  id: string; name: string; country: string; base_currency: string; industry: string;
  granted_tier: Tier; tier_label: string;
}
export interface Figure {
  key: string; label: string; value: number; currency: string; evidence: string[];
  evidence_count: number; formula: string; hint: string;
}
export interface DraftLine { account_id: string; debit: number; credit: number; currency: string; memo: string }
export interface DraftEntry { entry_date: string; memo: string; lines: DraftLine[]; reason: string; evidence: string[] }
export interface Finding {
  id: string; kind: string; title: string; explanation: string; amount: number; currency: string;
  confidence: number; evidence: string[]; proposed_entries: DraftEntry[];
}
export interface Channel {
  channel: string; label: string; book_balance: number; verified_balance: number; difference: number;
  currency: string; evidence: string[];
}
export interface CashTruth {
  company_id: string; as_of: string; currency: string; book_cash: Figure; verified_cash: Figure; gap: Figure;
  explained: Figure; unexplained: Figure; channels: Channel[]; findings: Finding[]; proposed_entries: DraftEntry[];
  summary: string;
}
export interface PnLLine { key: string; label: string; amount: number; evidence: string[]; evidence_count: number; hint: string }
export interface Pnl {
  company_id: string; month: string; currency: string; basis: string;
  revenue: Figure; cogs: Figure; gross_profit: Figure; gross_margin_pct: number | null;
  operating_expenses: Figure; operating_profit: Figure; net_profit: Figure; cash_in: Figure; cash_out: Figure;
  lines: PnLLine[]; comparison_prev_month: Record<string, number> | null; notes: string[]; summary: string;
}
export interface Kpi {
  key: string; label: string; value: number | null; unit: string; currency: string | null; period: string;
  status: "good" | "watch" | "bad" | "unknown"; evidence: string[]; explanation: string; hint: string;
}
export interface Kpis { company_id: string; as_of: string; currency: string; kpis: Kpi[]; summary: string }
export interface FcPoint {
  period_start: string; period_end: string; inflow: number; outflow: number; net: number; closing_cash: number;
  closing_cash_lower: number; closing_cash_upper: number; closing_cash_lower_95: number | null; closing_cash_upper_95: number | null;
}
export interface Forecast {
  company_id: string; as_of: string; horizon: number; granularity: "week" | "month"; currency: string;
  opening_cash: Figure; interval_level: number; points: FcPoint[]; method: string; assumptions: string[];
  backtest: { method: string; metric: string; value: number; horizon: number; folds: number; interval_coverage: number | null; explanation: string } | null;
  summary: string;
}
export interface Alert {
  id: string; severity: "info" | "warning" | "critical"; kind: string; title: string; message: string;
  due_date: string | null; amount: number | null; currency: string | null; evidence: string[]; suggested_action: string;
}
export interface Alerts { company_id: string; as_of: string; alerts: Alert[]; summary: string }
export interface Check { rule: string; passed: boolean; reason: string }
export interface FinMatch {
  product: {
    id: string; provider: string; name: string; country: string; product_type: string; currency: string;
    min_amount: number | null; max_amount: number | null; apr_min_pct: number | null; apr_max_pct: number | null;
    tenor_months_min: number | null; tenor_months_max: number | null; requirements: string[]; source_url: string;
  };
  eligible: boolean; fit_score: number; checks: Check[];
  affordability: null | {
    requested_amount: number; monthly_repayment: number; dscr: number | null; min_forecast_cash_after: number | null;
    affordable: boolean; explanation: string;
  };
  explanation: string; evidence: string[];
}
export interface Financing { company_id: string; as_of: string; currency: string; matches: FinMatch[]; disclaimer: string; summary: string }
export interface BriefAction {
  id: string; kind: string; title: string; why: string; amount: number | null; currency: string | null;
  finding_id: string | null; alert_id: string | null; draft: DraftEntry | null; approval_id: string | null;
  product_id: string | null; evidence: string[];
}
export interface Brief {
  company_id: string; as_of: string; week_start: string; week_end: string; currency: string; headline: string;
  cash_truth_gap: Figure; verified_cash: Figure; gap_pct_of_revenue: number | null; forecast_end_cash: number | null;
  forecast_lowest_cash: number | null; forecast_lowest_week: string | null; top_alerts: Alert[]; actions: BriefAction[];
  kpis: Kpi[]; summary: string;
  summaries?: Record<string, string>; // optional per-language text, if the backend provides it
}
export interface Approval {
  id: string; company_id: string; tool: string; tier: string; status: string;
  [k: string]: unknown;
}
export interface AuditRow {
  seq: number; ts: string; company_id: string | null; actor: string; action: string; tool: string | null;
  tier: string | null; payload_json: string; row_hash: string; prev_hash: string;
}
export interface Connector { [k: string]: unknown }
export interface EvidenceRow { ref: string; table: string; row: Record<string, unknown> }
