export function money(v: number | null | undefined, cur?: string | null, opts: { sign?: boolean; compact?: boolean } = {}): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  const compact = opts.compact && abs >= 10000;
  const s = new Intl.NumberFormat("en-US", {
    maximumFractionDigits: compact ? 1 : abs < 100 ? 2 : 0,
    notation: compact ? "compact" : "standard",
  }).format(abs);
  const sign = v < 0 ? "−" : opts.sign && v > 0 ? "+" : "";
  return `${sign}${cur ? cur + " " : ""}${s}`;
}
export const pct = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${v.toFixed(d)}%`);
export function shortDate(s: string | null | undefined): string {
  if (!s) return "";
  const d = new Date(s.length === 10 ? s + "T00:00:00" : s);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}
export function dateTime(s: string): string {
  const d = new Date(s);
  return d.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
export const KIND_LABEL: Record<string, string> = {
  unrecorded_expense: "Money left, not in your books",
  unrecorded_receipt: "Money came in, not in your books",
  stale_receivable: "Customer unpaid for too long",
  duplicate_entry: "Booked twice",
  timing_difference: "Timing difference",
  fx_difference: "Currency conversion difference",
  fee_not_recorded: "Fees not in your books",
  cash_count_shortfall: "Cash count short",
  other: "Other",
};
