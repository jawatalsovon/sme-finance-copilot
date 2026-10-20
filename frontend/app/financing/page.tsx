"use client";
import { useApp, useApi } from "@/components/AppContext";
import { Badge, Card, ErrorBox, EvidenceLink, Hint, Loading, PageTitle } from "@/components/ui";
import { money } from "@/lib/format";
import type { Financing } from "@/lib/types";

const TYPE: Record<string, string> = {
  term_loan: "Term loan", working_capital: "Working capital", overdraft: "Overdraft", invoice_financing: "Invoice financing",
  trade_finance: "Trade finance", microfinance: "Microfinance", govt_scheme: "Government scheme", lease: "Lease", other: "Other",
};

export default function FinancingPage() {
  const { companyId } = useApp();
  const { data, error, loading, status } = useApi<Financing>(`/reports/${companyId}/financing`);
  const sorted = [...(data?.matches ?? [])].sort((a, b) => Number(b.eligible) - Number(a.eligible) || b.fit_score - a.fit_score);
  return (
    <>
      <PageTitle title="Financing" sub="Public products that might suit this business, checked against your real numbers. For information only." />
      <div className="mb-5 rounded-xl border border-warn/30 bg-warn-soft p-4 text-sm text-warn">
        <b>Illustrative.</b> {data?.disclaimer ?? "Illustrative public information only. Not financial advice and not an offer. The copilot never applies for credit on your behalf."}
      </div>
      {loading && <Loading what="Matching products" />}
      {error && <ErrorBox error={error} status={status} />}
      <div className="space-y-4">
        {sorted.map((m) => (
          <Card key={m.product.id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-lg font-semibold">{m.product.name}</h3>
                  <Badge tone="neutral">{TYPE[m.product.product_type] ?? m.product.product_type}</Badge>
                  <Badge tone="info">illustrative</Badge>
                </div>
                <div className="text-sm text-muted">{m.product.provider} · {m.product.country}</div>
              </div>
              <div className="text-right">
                <Badge tone={m.eligible ? "good" : "bad"}>{m.eligible ? "You appear to qualify" : "Does not fit today"}</Badge>
                <div className="mt-1 text-sm text-muted"><Hint text="How well the product matches your size, history and needs, from 0 to 100.">Fit</Hint> <b className="num text-ink">{Math.round(m.fit_score * 100)}/100</b></div>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted">
              {(m.product.min_amount != null || m.product.max_amount != null) && <span>Amount: <b className="num text-ink">{m.product.min_amount != null ? money(m.product.min_amount, m.product.currency) : "any"} – {m.product.max_amount != null ? money(m.product.max_amount, m.product.currency) : "n/a"}</b></span>}
              {(m.product.apr_min_pct != null || m.product.apr_max_pct != null) && <span><Hint text="APR = the yearly cost of borrowing as a percentage of the amount.">APR</Hint>: <b className="num text-ink">{m.product.apr_min_pct ?? "?"}–{m.product.apr_max_pct ?? "?"}%</b></span>}
              {m.product.tenor_months_max != null && <span>Term: up to <b className="text-ink">{m.product.tenor_months_max} months</b></span>}
            </div>
            <p className="mt-3 text-[15px]">{m.explanation}</p>
            <div className="mt-3 grid gap-4 md:grid-cols-2">
              <div>
                <div className="mb-1 text-sm font-semibold">Why it fits or fails</div>
                <ul className="space-y-1 text-sm">
                  {m.checks.map((c, i) => (
                    <li key={i} className="flex gap-2"><span className={c.passed ? "text-good" : "text-bad"}>{c.passed ? "✓" : "✕"}</span><span>{c.reason}</span></li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="mb-1 text-sm font-semibold">Could you afford it?</div>
                {m.affordability ? (
                  <div className="rounded-xl bg-canvas p-3 text-sm">
                    <div>Borrow <b className="num">{money(m.affordability.requested_amount, data?.currency)}</b> → about <b className="num">{money(m.affordability.monthly_repayment, data?.currency)}</b> a month</div>
                    {m.affordability.dscr != null && <div><Hint text="Debt-service cover: how many times your cash flow covers the repayments. Above 1.25 is usually comfortable.">Repayment cover</Hint>: <b className="num">{m.affordability.dscr.toFixed(2)}×</b></div>}
                    {m.affordability.min_forecast_cash_after != null && <div>Lowest forecast cash after: <b className="num">{money(m.affordability.min_forecast_cash_after, data?.currency)}</b></div>}
                    <div className="mt-1"><Badge tone={m.affordability.affordable ? "good" : "bad"}>{m.affordability.affordable ? "affordable" : "too tight"}</Badge></div>
                    <p className="mt-1 text-muted">{m.affordability.explanation}</p>
                  </div>
                ) : <p className="text-sm text-muted">No affordability check for this product.</p>}
              </div>
            </div>
            <div className="mt-3 flex items-center gap-4 text-sm">
              <EvidenceLink refs={m.evidence} title={m.product.name} />
              {m.product.source_url && <a href={m.product.source_url} target="_blank" rel="noreferrer" className="text-brand hover:underline">Public source ↗</a>}
              <span className="text-muted">Needs: {m.product.requirements.join(", ") || "—"}</span>
            </div>
          </Card>
        ))}
      </div>
    </>
  );
}
