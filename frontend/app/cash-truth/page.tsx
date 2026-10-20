"use client";
import { useMemo, useState } from "react";
import { useApp, useApi } from "@/components/AppContext";
import { Badge, Card, ErrorBox, EvidenceLink, Hint, Loading, PageTitle, Stat } from "@/components/ui";
import { KIND_LABEL, money } from "@/lib/format";
import type { CashTruth, Finding } from "@/lib/types";

function FindingRow({ f, cur }: { f: Finding; cur: string }) {
  const [open, setOpen] = useState(false);
  const draft = f.proposed_entries[0];
  return (
    <div className="border-t border-line">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-4 px-4 py-3 text-left hover:bg-slate-50">
        <span className="min-w-0 flex-1 truncate font-medium">{f.title}</span>
        <Badge tone={f.confidence >= 0.8 ? "good" : f.confidence >= 0.5 ? "warn" : "neutral"} title="How sure the copilot is about the cause">{Math.round(f.confidence * 100)}% sure</Badge>
        <span className={`num w-40 text-right font-semibold ${f.amount < 0 ? "text-bad" : "text-good"}`}>{money(f.amount, f.currency || cur, { sign: true })}</span>
        <span className="text-muted">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="space-y-3 bg-canvas px-4 pb-4 pt-2">
          <p className="text-[15px]"><b>Likely cause: </b>{f.explanation}</p>
          <div className="flex items-center gap-2"><EvidenceLink refs={f.evidence} title={f.title} /></div>
          {draft ? (
            <div className="rounded-xl border border-line bg-white p-3">
              <div className="mb-1 flex items-center gap-2 text-sm font-semibold">Proposed correcting entry <Badge tone="warn">draft · needs your approval</Badge></div>
              <p className="text-sm text-muted">{draft.reason}</p>
              <table className="mt-2 w-full text-sm">
                <thead><tr className="text-left text-xs uppercase tracking-wide text-muted"><th>Account</th><th className="text-right">Debit</th><th className="text-right">Credit</th></tr></thead>
                <tbody>{draft.lines.map((l, i) => (
                  <tr key={i} className="border-t border-line"><td className="py-1 font-mono text-xs">{l.account_id}</td>
                    <td className="num text-right">{l.debit ? money(l.debit, l.currency) : ""}</td><td className="num text-right">{l.credit ? money(l.credit, l.currency) : ""}</td></tr>
                ))}</tbody>
              </table>
              <p className="mt-2 text-xs text-muted">Entry date {draft.entry_date}. Ask the copilot (in chat, with draft permission) to queue it for approval; nothing is written until you approve.</p>
            </div>
          ) : <p className="text-sm text-muted">No correction needed: this explains the gap but does not change your books.</p>}
        </div>
      )}
    </div>
  );
}

function Group({ kind, items, cur }: { kind: string; items: Finding[]; cur: string }) {
  const [open, setOpen] = useState(false);
  const [limit, setLimit] = useState(8);
  const total = items.reduce((s, f) => s + f.amount, 0);
  const sorted = useMemo(() => [...items].sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount)), [items]);
  return (
    <Card className="!p-0 overflow-hidden">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-4 px-5 py-4 text-left">
        <span className="text-muted">{open ? "▾" : "▸"}</span>
        <span className="flex-1 text-lg font-semibold">{KIND_LABEL[kind] ?? kind}</span>
        <Badge tone="neutral">{items.length} finding{items.length === 1 ? "" : "s"}</Badge>
        <span className={`num w-44 text-right text-lg font-semibold ${total < 0 ? "text-bad" : "text-good"}`}>{money(total, cur, { sign: true })}</span>
      </button>
      {open && (
        <>
          {sorted.slice(0, limit).map((f) => <FindingRow key={f.id} f={f} cur={cur} />)}
          {sorted.length > limit && <button onClick={() => setLimit(limit + 20)} className="w-full border-t border-line py-2.5 text-sm font-medium text-brand hover:bg-slate-50">Show more ({sorted.length - limit} left)</button>}
        </>
      )}
    </Card>
  );
}

export default function CashTruthPage() {
  const { companyId } = useApp();
  const { data: ct, error, loading, status } = useApi<CashTruth>(`/reports/${companyId}/cash-truth`);
  const groups = useMemo(() => {
    const m = new Map<string, Finding[]>();
    (ct?.findings ?? []).forEach((f) => m.set(f.kind, [...(m.get(f.kind) ?? []), f]));
    return [...m.entries()].sort((a, b) => Math.abs(b[1].reduce((s, f) => s + f.amount, 0)) - Math.abs(a[1].reduce((s, f) => s + f.amount, 0)));
  }, [ct]);

  return (
    <>
      <PageTitle title="Cash Truth" sub="Where your books and your real bank, wallet and cash balances disagree, and why." />
      {loading && <Loading what="Comparing books with statements" />}
      {error && <ErrorBox error={error} status={status} />}
      {ct && (
        <div className="space-y-6">
          <Card>
            <div className="grid gap-6 md:grid-cols-4">
              <Stat fig={ct.book_cash} />
              <Stat fig={ct.verified_cash} />
              <Stat fig={ct.gap} tone={ct.gap.value < 0 ? "text-bad" : "text-good"} />
              <Stat fig={ct.unexplained} />
            </div>
            <p className="mt-4 text-[15px] text-muted">{ct.summary}</p>
          </Card>

          <Card>
            <h3 className="mb-3 text-lg font-semibold">By account</h3>
            <table className="w-full text-[15px]">
              <thead><tr className="text-left text-xs uppercase tracking-wide text-muted"><th className="py-2">Account</th><th className="text-right">Books say</th><th className="text-right"><Hint text="From the bank/wallet statement or the physical cash count.">Real</Hint></th><th className="text-right">Difference</th><th /></tr></thead>
              <tbody>
                {ct.channels.map((c, i) => (
                  <tr key={i} className="border-t border-line">
                    <td className="py-2">{c.label}</td>
                    <td className="num text-right">{money(c.book_balance, c.currency)}</td>
                    <td className="num text-right">{money(c.verified_balance, c.currency)}</td>
                    <td className={`num text-right font-semibold ${c.difference < 0 ? "text-bad" : c.difference > 0 ? "text-good" : ""}`}>{money(c.difference, c.currency, { sign: true })}</td>
                    <td className="text-right"><EvidenceLink refs={c.evidence} title={c.label} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <div>
            <h3 className="mb-3 text-lg font-semibold">What explains the gap <span className="text-sm font-normal text-muted">· {ct.findings.length} findings, grouped by cause</span></h3>
            <div className="space-y-3">{groups.map(([k, items]) => <Group key={k} kind={k} items={items} cur={ct.currency} />)}</div>
          </div>
        </div>
      )}
    </>
  );
}
