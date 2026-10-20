"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "@/components/AppContext";
import { Badge, EvidenceLink, TierBadge } from "@/components/ui";
import { API_BASE } from "@/lib/api";
import { ApprovalLink } from "./ApprovalLink";

interface TierDecision { tool: string; tool_tier: string; granted_tier: string; decision: string; reason: string }
interface Step {
  key: string; tool: string; tier: string; args?: unknown; status: "running" | "executed" | "queued_for_approval" | "denied" | "error" | string;
  summary?: string; evidence?: string[]; evidence_count?: number; approval_id?: string; error?: string; decision?: TierDecision;
}
interface Turn {
  id: number; question: string; steps: Step[]; answer?: string; shown?: number; evidenceIds: string[]; refusals: string[];
  policyMessage?: string; unverified: string[]; approvals: string[]; error?: string; done: boolean; provider?: string;
}

const TOOL_LABEL: Record<string, string> = {
  cash_truth: "compared your books with bank statements and cash counts",
  account_balances: "checked your book balances",
  search_transactions: "searched bank and ledger lines",
  get_source_rows: "opened the source rows",
  monthly_pnl: "worked out last month's profit",
  kpis: "calculated your key numbers",
  cashflow_forecast: "forecast your cash",
  alerts: "looked for things that need attention",
  financing_matches: "matched financing products",
  run_scenario: "ran a what-if",
  list_accounts: "read your chart of accounts",
  draft_journal_entry: "prepared a correcting entry (draft)",
  connector_write_draft: "prepared a draft in a connected system",
  mark_reminder_ready: "prepared a payment reminder",
};

const SUGGESTIONS = [
  "What is my cash position?",
  "Run cash truth: do my books match the bank?",
  "Will I run out of cash in the next few weeks?",
  "Which customers are paying late?",
  "Can I get a loan, and could I afford it?",
  "Please transfer 50,000 to my supplier",
];

const NUM = /-?\d[\d,]*(?:\.\d+)?/g;
const linkable = (n: string) => /[.,]/.test(n) ? n.replace(/[^\d]/g, "").length >= 3 : n.replace(/[^\d]/g, "").length >= 5;
const norm = (s: string) => { const n = Number(s.replace(/,/g, "")); return Number.isFinite(n) ? String(Math.abs(n)) : s; };

function Answer({ text, steps, openEvidence }: { text: string; steps: Step[]; openEvidence: (t: string, r: string[]) => void }) {
  const map = useMemo(() => {
    const m = new Map<string, { refs: string[]; title: string }>();
    for (const s of steps) {
      if (!s.evidence?.length || !s.summary) continue;
      for (const n of s.summary.match(NUM) ?? []) {
        const k = norm(n);
        if (linkable(n) && !m.has(k)) m.set(k, { refs: s.evidence, title: TOOL_LABEL[s.tool] ?? s.tool });
      }
    }
    return m;
  }, [steps]);
  const clean = text.replace(/\s*\[[a-z_]+:[^\]\s]+\]/g, "");
  const parts: React.ReactNode[] = [];
  let last = 0; let i = 0;
  for (const m of clean.matchAll(NUM)) {
    const hit = map.get(norm(m[0]));
    const idx = m.index ?? 0;
    if (hit && linkable(m[0])) {
      parts.push(clean.slice(last, idx));
      parts.push(
        <button key={i++} onClick={() => openEvidence(`Evidence: ${hit.title}`, hit.refs)}
          className="num rounded bg-brand-soft px-1 font-semibold text-brand underline decoration-dotted underline-offset-2 hover:bg-brand hover:text-white"
          title="Show the source rows behind this number">{m[0]}</button>,
      );
      last = idx + m[0].length;
    }
  }
  parts.push(clean.slice(last));
  return <div className="whitespace-pre-wrap text-[16px] leading-relaxed">{parts}</div>;
}

export default function ChatPage() {
  const { companyId, company, openEvidence, bump } = useApp();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const session = useRef<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => { session.current = null; setTurns([]); }, [companyId]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns]);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("q");
    if (q) setInput(q);
  }, []);

  // typewriter reveal of the final answer
  useEffect(() => {
    const t = setInterval(() => {
      setTurns((ts) => ts.some((x) => x.answer && (x.shown ?? 0) < x.answer.length)
        ? ts.map((x) => (x.answer && (x.shown ?? 0) < x.answer.length ? { ...x, shown: Math.min(x.answer.length, (x.shown ?? 0) + 18) } : x))
        : ts);
    }, 16);
    return () => clearInterval(t);
  }, []);

  const patch = (id: number, fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t)));

  async function send(message: string) {
    if (!message.trim() || busy) return;
    const id = Date.now();
    setTurns((ts) => [...ts, { id, question: message, steps: [], evidenceIds: [], refusals: [], unverified: [], approvals: [], done: false }]);
    setInput(""); setBusy(true);
    const ctl = new AbortController(); abort.current = ctl;
    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-Actor": "owner" }, signal: ctl.signal,
        body: JSON.stringify({ company_id: companyId, message, session_id: session.current }),
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let cut: number;
        while ((cut = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, cut); buf = buf.slice(cut + 2);
          const data = block.split("\n").find((l) => l.startsWith("data:"));
          if (!data) continue;
          handle(id, JSON.parse(data.slice(5).trim()));
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") patch(id, (t) => ({ ...t, error: `Could not reach the copilot: ${(e as Error).message}` }));
    }
    patch(id, (t) => ({ ...t, done: true }));
    setBusy(false); bump();
  }

  function handle(id: number, ev: Record<string, any>) { // eslint-disable-line @typescript-eslint/no-explicit-any
    if (ev.type === "session") { session.current = ev.session_id; patch(id, (t) => ({ ...t, provider: ev.provider })); }
    else if (ev.type === "step") {
      if (ev.kind === "tool_call") patch(id, (t) => ({ ...t, steps: [...t.steps, { key: `${ev.step}-${ev.tool}-${t.steps.length}`, tool: ev.tool, tier: ev.tier, args: ev.args, status: "running" }] }));
      else if (ev.kind === "tool_result") patch(id, (t) => {
        const steps = [...t.steps];
        let k = steps.length - 1;
        while (k >= 0 && !(steps[k].tool === ev.tool && steps[k].status === "running")) k--;
        const upd: Step = { ...(steps[k] ?? { key: `r-${steps.length}`, tool: ev.tool, tier: ev.tier_decision?.tool_tier ?? "T0" }), status: ev.status, summary: ev.summary, evidence: ev.evidence, evidence_count: ev.evidence_count, approval_id: ev.approval_id, error: ev.error, decision: ev.tier_decision };
        if (k >= 0) steps[k] = upd; else steps.push(upd);
        return { ...t, steps };
      });
      else if (ev.kind === "policy") patch(id, (t) => ({ ...t, policyMessage: ev.message, refusals: [...t.refusals, ev.policy] }));
    } else if (ev.type === "final") {
      patch(id, (t) => ({ ...t, answer: ev.answer, shown: 0, evidenceIds: ev.evidence_ids ?? [], refusals: ev.refusals?.length ? ev.refusals : t.refusals, unverified: ev.unverified_numbers ?? [], approvals: ev.approvals ?? [] }));
    } else if (ev.type === "error") patch(id, (t) => ({ ...t, error: `${ev.code}: ${ev.message}` }));
  }

  return (
    <div className="flex h-[calc(100vh-150px)] flex-col">
      <div className="mb-3 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Ask the copilot</h1>
          <p className="mt-1 text-[15px] text-muted">Plain-language answers about {company?.name ?? "your business"}. Every number links to the rows it came from.</p>
        </div>
        {company && <TierBadge tier={company.granted_tier} label={company.tier_label} />}
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto rounded-2xl border border-line bg-white p-6">
        {turns.length === 0 && (
          <div className="mx-auto mt-6 max-w-2xl text-center">
            <p className="text-lg font-medium">What would you like to know?</p>
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)} className="rounded-full border border-line bg-canvas px-4 py-2 text-sm hover:border-brand hover:text-brand">{s}</button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t) => (
          <div key={t.id} className="space-y-3">
            <div className="flex justify-end"><div className="max-w-[70%] rounded-2xl rounded-br-sm bg-ink px-4 py-2.5 text-white">{t.question}</div></div>
            {t.steps.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {t.steps.map((s) => (
                  <span key={s.key} title={s.summary ?? s.error ?? ""}
                    className={`inline-flex max-w-full items-center gap-2 rounded-full border px-3 py-1 text-xs ${s.status === "denied" ? "border-bad/40 bg-bad-soft text-bad" : s.status === "queued_for_approval" ? "border-warn/40 bg-warn-soft text-warn" : "border-line bg-canvas text-slate-700"}`}>
                    {s.status === "running" ? <span className="h-2 w-2 animate-pulse rounded-full bg-brand" /> : s.status === "denied" || s.status === "error" ? "✕" : "✓"}
                    <span>{TOOL_LABEL[s.tool] ?? s.tool}</span>
                    <TierBadge tier={s.decision?.tool_tier ?? s.tier} />
                    {s.status === "queued_for_approval" && <b>needs your approval</b>}
                    {s.status === "denied" && <b>blocked: {s.decision?.reason ?? s.error}</b>}
                    {s.evidence && s.evidence.length > 0 && (
                      <button onClick={() => openEvidence(TOOL_LABEL[s.tool] ?? s.tool, s.evidence!)} className="font-semibold text-brand underline">{s.evidence_count ?? s.evidence.length} rows</button>
                    )}
                  </span>
                ))}
              </div>
            )}
            {t.refusals.length > 0 && (
              <div className="rounded-xl border border-bad/30 bg-bad-soft p-4">
                <div className="font-semibold text-bad">🚫 Not something I can do</div>
                <p className="mt-1 text-[15px]">{t.policyMessage ?? "I can't move money, make payments or transfer funds. That is outside what this copilot is allowed to do at any permission level."}</p>
                <p className="mt-1 text-sm text-muted">Refusal recorded in the audit log.</p>
              </div>
            )}
            {t.answer && (
              <div className="max-w-[85%] rounded-2xl rounded-bl-sm border border-line bg-canvas px-4 py-3">
                {(t.shown ?? 0) < t.answer.length
                  ? <div className="whitespace-pre-wrap text-[16px] leading-relaxed">{t.answer.slice(0, t.shown ?? 0).replace(/\s*\[[a-z_]+:[^\]\s]*$/, "")}<span className="animate-pulse">▍</span></div>
                  : <Answer text={t.answer} steps={t.steps} openEvidence={openEvidence} />}
                {(t.shown ?? 0) >= t.answer.length && (
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {t.evidenceIds.length > 0 && <EvidenceLink refs={t.evidenceIds} title="Evidence cited in this answer" label={`${t.evidenceIds.length} cited source rows`} />}
                    {t.approvals.map((a) => <ApprovalLink key={a} id={a} />)}
                    {t.unverified.length > 0 && <Badge tone="warn" title="These figures were not found in any tool output">⚠ unverified: {t.unverified.join(", ")}</Badge>}
                    {t.provider && <span className="text-xs text-muted">model: {t.provider}</span>}
                  </div>
                )}
              </div>
            )}
            {!t.done && !t.answer && !t.error && <div className="text-sm text-muted"><span className="animate-pulse">Working on it…</span></div>}
            {t.error && <div className="rounded-xl bg-bad-soft p-3 text-sm text-bad">{t.error}</div>}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="mt-3 flex gap-2">
        <input
          value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about cash, profit, late customers, forecasts, loans…"
          className="flex-1 rounded-xl border border-line bg-white px-4 py-3 text-[16px] outline-none focus:border-brand"
        />
        <button disabled={busy || !input.trim()} className="rounded-xl bg-brand px-6 py-3 font-semibold text-white disabled:opacity-50">{busy ? "…" : "Send"}</button>
      </form>
    </div>
  );
}
