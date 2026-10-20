"use client";
import { useMemo, useState } from "react";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApp, useApi } from "@/components/AppContext";
import { Badge, Card, ErrorBox, EvidenceLink, Hint, Loading, PageTitle, SEV_TONE, Stat } from "@/components/ui";
import { money, pct, shortDate } from "@/lib/format";
import type { Alerts, Forecast, Kpis, Pnl } from "@/lib/types";

function monthsBack(asOf: string, n: number) {
  const [y, m] = asOf.split("-").map(Number);
  return Array.from({ length: n }, (_, i) => {
    const d = new Date(y, m - 1 - i, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  });
}
const monthName = (m: string) => new Date(m + "-01T00:00:00").toLocaleDateString("en-GB", { month: "long", year: "numeric" });

function PnlSection({ id, asOf }: { id: string; asOf: string }) {
  const months = useMemo(() => monthsBack(asOf, 12), [asOf]);
  const [month, setMonth] = useState<string | null>(null);
  const m = month && months.includes(month) ? month : months[0];
  const { data: p, error, loading, status } = useApi<Pnl>(`/reports/${id}/pnl?month=${m}`);
  return (
    <Card>
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-lg font-semibold">How you did in {monthName(m)}</h3>
        <select value={m} onChange={(e) => setMonth(e.target.value)} className="rounded-lg border border-line px-3 py-1.5 text-sm">
          {months.map((x) => <option key={x} value={x}>{monthName(x)}</option>)}
        </select>
      </div>
      {loading && <Loading what="Working out the month" />}
      {error && <ErrorBox error={error} status={status} />}
      {p && (
        <>
          <p className="mb-4 rounded-xl bg-brand-soft p-4 text-[16px] leading-relaxed">
            You sold <b className="num">{money(p.revenue.value, p.currency)}</b>. What it cost to get those goods to customers was <b className="num">{money(p.cogs.value, p.currency)}</b>, leaving <b className="num">{money(p.gross_profit.value, p.currency)}</b>
            {p.gross_margin_pct != null && <> ({pct(p.gross_margin_pct)} of sales)</>}. Running the business cost <b className="num">{money(p.operating_expenses.value, p.currency)}</b>, so you {p.net_profit.value >= 0 ? "kept" : "lost"} <b className="num">{money(Math.abs(p.net_profit.value), p.currency)}</b>.
          </p>
          <div className="grid gap-6 md:grid-cols-5">
            {[p.revenue, p.gross_profit, p.operating_expenses, p.net_profit, p.cash_in].map((f) => <Stat key={f.key} fig={f} />)}
          </div>
          <div className="mt-4 grid gap-6 md:grid-cols-5">
            <div className="md:col-span-2"><Stat fig={p.cash_out} /></div>
            <div className="md:col-span-3 text-sm text-muted">
              <Hint text="Cash in and out is real money moving through the bank, wallet and till. It differs from profit because customers pay late and suppliers are paid on different days.">Why cash in/out differ from profit</Hint>
              {p.notes.map((n, i) => <p key={i} className="mt-1">• {n}</p>)}
            </div>
          </div>
          <details className="mt-5"><summary className="cursor-pointer text-sm font-medium text-brand">Every line behind these numbers ({p.lines.length})</summary>
          <table className="mt-2 w-full text-[15px]">
            <tbody>
              {p.lines.map((l) => (
                <tr key={l.key} className="border-t border-line">
                  <td className="py-2"><Hint text={l.hint}>{l.label}</Hint></td>
                  <td className="num text-right">{money(l.amount, p.currency)}</td>
                  <td className="w-40 text-right"><EvidenceLink refs={l.evidence} count={l.evidence_count} title={l.label} /></td>
                </tr>
              ))}
            </tbody>
          </table></details>
          <p className="mt-3 text-sm text-muted">{p.summary}</p>
        </>
      )}
    </Card>
  );
}

function ForecastSection({ id }: { id: string }) {
  const [mode, setMode] = useState<"week" | "month">("week");
  const path = mode === "week" ? `/reports/${id}/forecast?horizon=13&granularity=week` : `/reports/${id}/forecast?horizon=12&granularity=month`;
  const { data: f, error, loading, status } = useApi<Forecast>(path);
  const data = useMemo(() => (f?.points ?? []).map((p) => ({
    label: new Date(p.period_start + "T00:00:00").toLocaleDateString("en-GB", mode === "week" ? { day: "numeric", month: "short" } : { month: "short", year: "2-digit" }),
    close: p.closing_cash, band80: [p.closing_cash_lower, p.closing_cash_upper],
    band95: p.closing_cash_lower_95 != null && p.closing_cash_upper_95 != null ? [p.closing_cash_lower_95, p.closing_cash_upper_95] : undefined,
    inflow: p.inflow, outflow: p.outflow,
  })), [f, mode]);
  const cur = f?.currency;
  const lowest = f?.points.reduce((a, b) => (b.closing_cash_lower < a.closing_cash_lower ? b : a), f.points[0]);
  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold">Where your cash is heading</h3>
          <p className="text-sm text-muted">The line is the best guess; the shaded area is the range we are {f ? Math.round(f.interval_level * 100) : 80}% sure about (lighter = 95%).</p>
        </div>
        <div className="flex rounded-lg border border-line p-0.5">
          {([["week", "Next 13 weeks"], ["month", "Next 12 months"]] as const).map(([k, l]) => (
            <button key={k} onClick={() => setMode(k)} className={`rounded-md px-3 py-1 text-sm ${mode === k ? "bg-ink text-white" : "text-muted"}`}>{l}</button>
          ))}
        </div>
      </div>
      {loading && <Loading what="Forecasting" />}
      {error && <ErrorBox error={error} status={status} />}
      {f && (
        <>
          <div className="h-[340px]">
            <ResponsiveContainer>
              <ComposedChart data={data} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
                <CartesianGrid stroke="#e3e8ef" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 12, fill: "#5b6b80" }} />
                <YAxis tickFormatter={(v) => money(v, "", { compact: true })} tick={{ fontSize: 12, fill: "#5b6b80" }} width={70} />
                <Tooltip formatter={(v: unknown, n: unknown) => Array.isArray(v) ? `${money(v[0] as number, cur)} to ${money(v[1] as number, cur)}` : money(v as number, cur)}
                  labelFormatter={(l) => `Week/month of ${l}`} />
                <ReferenceLine y={0} stroke="#b42318" strokeDasharray="4 4" />
                {data.some((d) => d.band95) && <Area dataKey="band95" name="95% range" stroke="none" fill="#0b6b57" fillOpacity={0.1} isAnimationActive={false} />}
                <Area dataKey="band80" name={`${Math.round(f.interval_level * 100)}% range`} stroke="none" fill="#0b6b57" fillOpacity={0.22} isAnimationActive={false} />
                <Line dataKey="close" name="Expected cash" stroke="#0b6b57" strokeWidth={2.5} dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 grid gap-3 text-sm md:grid-cols-3">
            <div><span className="text-muted">Cash today: </span><b className="num">{money(f.opening_cash.value, f.currency)}</b> <EvidenceLink refs={f.opening_cash.evidence} count={f.opening_cash.evidence_count} title="Opening cash" /></div>
            {lowest && <div><span className="text-muted">Worst case low point: </span><b className="num">{money(lowest.closing_cash_lower, f.currency)}</b> ({shortDate(lowest.period_start)})</div>}
            {f.backtest && <div><Hint text={f.backtest.explanation || "How far off past forecasts of this kind were when checked against what really happened."}><span className="text-muted">Past accuracy: </span></Hint><b>{f.backtest.metric} {f.backtest.value.toFixed(1)}%</b> over {f.backtest.folds} test periods</div>}
          </div>
          <p className="mt-2 text-sm text-muted">{f.summary}</p>
          {f.assumptions.length > 0 && <details className="mt-2 text-sm"><summary className="cursor-pointer font-medium text-brand">What this assumes</summary><ul className="mt-1 list-disc pl-5 text-muted">{f.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul></details>}
        </>
      )}
    </Card>
  );
}

export default function MoneyPage() {
  const { companyId } = useApp();
  const kpis = useApi<Kpis>(`/reports/${companyId}/kpis`);
  const alerts = useApi<Alerts>(`/reports/${companyId}/alerts`);
  const asOf = kpis.data?.as_of;
  return (
    <>
      <PageTitle title="Money & forecast" sub="Profit in plain words, where cash came from and went, and where it is heading. Hover dotted terms for a one-line explanation." />
      <div className="space-y-6">
        {kpis.error && <ErrorBox error={kpis.error} status={kpis.status} />}
        {kpis.data && (
          <div className="grid gap-4 md:grid-cols-4">
            {kpis.data.kpis.slice(0, 8).map((k) => (
              <Card key={k.key} className="!p-4">
                <div className="text-sm text-muted"><Hint text={k.hint || k.explanation}>{k.label}</Hint></div>
                <div className="num text-2xl font-semibold">{k.value == null ? "—" : k.unit === "pct" ? pct(k.value) : k.unit === "currency" ? money(k.value, k.currency) : `${k.value.toFixed(1)} ${k.unit}`}</div>
                <div className="mt-1 flex items-center gap-2"><Badge tone={k.status === "good" ? "good" : k.status === "bad" ? "bad" : k.status === "watch" ? "warn" : "neutral"}>{k.status}</Badge><EvidenceLink refs={k.evidence} title={k.label} /></div>
              </Card>
            ))}
          </div>
        )}
        {asOf && <PnlSection id={companyId} asOf={asOf} />}
        <ForecastSection id={companyId} />
        <Card>
          <h3 className="mb-3 text-lg font-semibold">Alerts</h3>
          {alerts.loading && <Loading what="Checking" />}
          {alerts.error && <ErrorBox error={alerts.error} status={alerts.status} />}
          <ul className="space-y-3">
            {alerts.data?.alerts.map((a) => (
              <li key={a.id} className="rounded-xl border border-line p-3">
                <div className="flex items-start justify-between gap-2"><span className="font-medium">{a.title}</span><Badge tone={SEV_TONE[a.severity]}>{a.severity}</Badge></div>
                <p className="mt-1 text-sm text-muted">{a.message}</p>
                {a.suggested_action && <p className="mt-1 text-sm"><b>Next step:</b> {a.suggested_action}</p>}
                <div className="mt-2 flex items-center gap-3 text-xs text-muted">{a.due_date && <span>Due {shortDate(a.due_date)}</span>}<EvidenceLink refs={a.evidence} title={a.title} /></div>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}
