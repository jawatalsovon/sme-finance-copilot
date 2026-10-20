"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useApp, useApi } from "@/components/AppContext";
import { ApprovalCard } from "@/components/ApprovalCard";
import { Badge, Card, Empty, ErrorBox, EvidenceLink, Hint, Loading, PageTitle, SEV_TONE, Stat } from "@/components/ui";
import { get } from "@/lib/api";
import { money, pct, shortDate } from "@/lib/format";
import type { Alerts, Approval, Brief, BriefAction, CashTruth, Forecast, Kpis } from "@/lib/types";

const LANGS = [["en", "EN"], ["zh", "中文"], ["bn", "বাংলা"]] as const;

/** Use the backend owner-brief when it exists, otherwise assemble the same facts from the report endpoints. */
async function loadBrief(id: string): Promise<{ brief: Brief; assembled: boolean }> {
  try {
    return { brief: await get<Brief>(`/reports/${id}/owner-brief`), assembled: false };
  } catch (e) {
    if ((e as { status?: number }).status !== 404 && (e as { status?: number }).status !== 501) throw e;
  }
  const [ct, al, kp, fc] = await Promise.all([
    get<CashTruth>(`/reports/${id}/cash-truth`), get<Alerts>(`/reports/${id}/alerts`),
    get<Kpis>(`/reports/${id}/kpis`), get<Forecast>(`/reports/${id}/forecast?horizon=13&granularity=week`),
  ]);
  const low = fc.points.reduce((m, p) => (p.closing_cash < m.closing_cash ? p : m), fc.points[0]);
  const top = [...ct.findings].filter((f) => f.proposed_entries.length).sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount)).slice(0, 3);
  const actions: BriefAction[] = top.map((f, i) => ({
    id: `act-${i + 1}`, kind: "approve_draft", title: f.title, why: f.explanation, amount: f.amount, currency: f.currency,
    finding_id: f.id, alert_id: null, draft: f.proposed_entries[0], approval_id: null, product_id: null, evidence: f.evidence,
  }));
  const gap = ct.gap;
  return {
    assembled: true,
    brief: {
      company_id: id, as_of: ct.as_of, week_start: ct.as_of, week_end: ct.as_of, currency: ct.currency,
      headline: gap.value < 0 ? `Your books show ${money(Math.abs(gap.value), ct.currency)} more cash than you can prove.` : "Your books and real cash agree.",
      cash_truth_gap: gap, verified_cash: ct.verified_cash, gap_pct_of_revenue: null,
      forecast_end_cash: fc.points[fc.points.length - 1]?.closing_cash ?? null, forecast_lowest_cash: low?.closing_cash ?? null,
      forecast_lowest_week: low?.period_start ?? null, top_alerts: al.alerts.slice(0, 3), actions, kpis: kp.kpis.slice(0, 4), summary: ct.summary,
    },
  };
}

export default function BriefPage() {
  const { companyId, company, version } = useApp();
  const [lang, setLang] = useState<string>("en");
  const [state, setState] = useState<{ brief?: Brief; assembled?: boolean; error?: string; loading: boolean }>({ loading: true });
  const approvals = useApi<Approval[]>(`/approvals?company_id=${companyId}&status=pending`);

  useEffect(() => {
    let live = true;
    setState({ loading: true });
    loadBrief(companyId).then((r) => live && setState({ ...r, loading: false })).catch((e) => live && setState({ error: String(e.message), loading: false }));
    return () => { live = false; };
  }, [companyId, version]);

  const b = state.brief;
  const langAvailable = (l: string) => l === "en" || !!b?.summaries?.[l];
  const pending = approvals.data ?? [];

  return (
    <>
      <PageTitle
        title="Owner Brief"
        sub={b ? `Week of ${shortDate(b.week_start)} · figures as of ${shortDate(b.as_of)}` : "Your weekly brief, prepared by the copilot without being asked."}
        right={
          <div className="flex rounded-lg border border-line bg-white p-0.5" title="Translations appear here when the backend provides them">
            {LANGS.map(([k, label]) => (
              <button
                key={k} disabled={!langAvailable(k)} onClick={() => setLang(k)}
                className={`rounded-md px-3 py-1 text-sm ${lang === k ? "bg-ink text-white" : "text-muted"} disabled:cursor-not-allowed disabled:opacity-40`}
              >{label}</button>
            ))}
          </div>
        }
      />
      {state.loading && <Loading what="Preparing your brief (the first run for a business can take up to 30 seconds)" />}
      {state.error && <ErrorBox error={state.error} />}
      {b && (
        <div className="space-y-6">
          <Card className="border-l-4 border-l-bad">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted">Cash Truth</div>
            <h2 className="mt-1 text-2xl font-semibold leading-snug">{lang !== "en" && b.summaries?.[lang] ? b.summaries[lang] : b.headline}</h2>
            <div className="mt-5 grid gap-6 md:grid-cols-3">
              <div>
                <div className="text-sm text-muted">Books say</div>
                <div className="num text-3xl font-semibold">{money(b.verified_cash.value - b.cash_truth_gap.value, b.currency)}</div>
              </div>
              <div>
                <div className="text-sm text-muted">Real cash is</div>
                <div className="num text-3xl font-semibold text-brand">{money(b.verified_cash.value, b.currency)}</div>
                <div className="mt-1"><EvidenceLink refs={b.verified_cash.evidence} count={b.verified_cash.evidence_count} title="Verified cash" /></div>
              </div>
              <div>
                <div className="text-sm text-muted"><Hint text={b.cash_truth_gap.hint || "Real cash minus what your books say. Negative means the books overstate your cash."}>The gap</Hint></div>
                <div className={`num text-3xl font-semibold ${b.cash_truth_gap.value < 0 ? "text-bad" : "text-good"}`}>{money(b.cash_truth_gap.value, b.currency, { sign: true })}</div>
                <div className="mt-1 flex items-center gap-2">
                  <EvidenceLink refs={b.cash_truth_gap.evidence} count={b.cash_truth_gap.evidence_count} title="Cash Truth gap" />
                  {b.gap_pct_of_revenue != null && <Badge tone="warn">{pct(b.gap_pct_of_revenue)} of revenue</Badge>}
                </div>
              </div>
            </div>
            <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm text-muted">
              {b.forecast_lowest_cash != null && <span>Lowest cash in the next 13 weeks: <b className="num text-ink">{money(b.forecast_lowest_cash, b.currency)}</b>{b.forecast_lowest_week && ` (week of ${shortDate(b.forecast_lowest_week)})`}</span>}
              {b.forecast_end_cash != null && <span>Expected cash after 13 weeks: <b className="num text-ink">{money(b.forecast_end_cash, b.currency)}</b></span>}
              <Link href="/cash-truth" className="ml-auto font-medium text-brand hover:underline">See every finding →</Link>
            </div>
          </Card>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <h3 className="mb-3 text-lg font-semibold">Needs your attention</h3>
              {b.top_alerts.length === 0 && <Empty>Nothing urgent this week.</Empty>}
              <ul className="space-y-3">
                {b.top_alerts.map((a) => (
                  <li key={a.id} className="rounded-xl border border-line p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="font-medium">{a.title}</div>
                      <Badge tone={SEV_TONE[a.severity]}>{a.severity}</Badge>
                    </div>
                    <p className="mt-1 text-sm text-muted">{a.message}</p>
                    {a.suggested_action && <p className="mt-1 text-sm"><b>Next step:</b> {a.suggested_action}</p>}
                    <div className="mt-2"><EvidenceLink refs={a.evidence} title={a.title} /></div>
                  </li>
                ))}
              </ul>
            </Card>

            <Card>
              <h3 className="mb-3 text-lg font-semibold">The business at a glance</h3>
              <div className="grid grid-cols-2 gap-3">
                {b.kpis.slice(0, 4).map((k) => (
                  <div key={k.key} className="rounded-xl bg-canvas p-3">
                    <div className="text-sm text-muted"><Hint text={k.hint || k.explanation}>{k.label}</Hint></div>
                    <div className="num text-xl font-semibold">{k.value == null ? "—" : k.unit === "pct" ? pct(k.value) : k.unit === "currency" ? money(k.value, k.currency) : `${k.value.toFixed(1)} ${k.unit}`}</div>
                    <Badge tone={k.status === "good" ? "good" : k.status === "bad" ? "bad" : k.status === "watch" ? "warn" : "neutral"}>{k.status}</Badge>
                  </div>
                ))}
              </div>
              <Link href="/money" className="mt-3 inline-block text-sm font-medium text-brand hover:underline">Open Money &amp; forecast →</Link>
            </Card>
          </div>

          <Card>
            <div className="mb-1 flex items-center justify-between">
              <h3 className="text-lg font-semibold">Recommended actions</h3>
              <span className="text-xs text-muted">The copilot drafts. You decide. Nothing is posted without your approval.</span>
            </div>
            {pending.length > 0 && (
              <div className="mt-3 space-y-3">
                <div className="text-sm font-medium text-warn">Waiting for your approval ({pending.length})</div>
                {pending.map((a) => <ApprovalCard key={a.id} a={a} currency={b.currency} />)}
              </div>
            )}
            {b.actions.length > 0 && (
              <div className="mt-5 space-y-3">
                <div className="text-sm font-medium text-muted">{pending.length ? "More suggestions" : "Suggestions"}</div>
                {b.actions.filter((x) => !(x.approval_id && pending.some((p) => p.id === x.approval_id)) && !pending.some((p) => x.draft && String(p.title ?? "").endsWith(x.draft.memo))).map((x) => (
                  <div key={x.id} className="rounded-xl border border-line p-4">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="font-medium">{x.title}</div>
                      {x.amount != null && <span className="num font-semibold">{money(x.amount, x.currency, { sign: true })}</span>}
                    </div>
                    <p className="mt-1 text-sm text-muted">{x.why}</p>
                    <div className="mt-2 flex items-center gap-3">
                      <EvidenceLink refs={x.evidence} title={x.title} />
                      <Link href={`/chat?q=${encodeURIComponent(`Draft the correcting entry for: ${x.title}`)}`} className="text-sm font-medium text-brand hover:underline">Ask the copilot to draft it →</Link>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {pending.length === 0 && b.actions.length === 0 && <Empty>No actions to review.</Empty>}
            {state.assembled && <p className="mt-3 text-xs text-muted">Brief assembled from the live reports ({company?.name}).</p>}
          </Card>
        </div>
      )}
    </>
  );
}
