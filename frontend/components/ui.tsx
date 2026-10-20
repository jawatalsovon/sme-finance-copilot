"use client";
import type { ReactNode } from "react";
import { useApp } from "./AppContext";
import { money } from "@/lib/format";
import type { Figure } from "@/lib/types";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-2xl border border-line bg-white p-5 shadow-[0_1px_2px_rgba(15,27,45,.04)] ${className}`}>{children}</section>;
}

export function PageTitle({ title, sub, right }: { title: string; sub?: string; right?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {sub && <p className="mt-1 max-w-3xl text-[15px] text-muted">{sub}</p>}
      </div>
      {right}
    </div>
  );
}

const TONES: Record<string, string> = {
  good: "bg-good-soft text-good", bad: "bg-bad-soft text-bad", warn: "bg-warn-soft text-warn",
  info: "bg-info-soft text-info", neutral: "bg-slate-100 text-slate-600", brand: "bg-brand-soft text-brand",
};
export function Badge({ tone = "neutral", children, title }: { tone?: keyof typeof TONES; children: ReactNode; title?: string }) {
  return <span title={title} className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${TONES[tone]}`}>{children}</span>;
}

export function TierBadge({ tier, label }: { tier: string; label?: string }) {
  const tone = tier === "T0" ? "neutral" : tier === "T1" ? "info" : tier === "T2" ? "warn" : "bad";
  const names: Record<string, string> = { T0: "Read", T1: "Analyse", T2: "Draft · needs approval", T3: "Limited action" };
  return <Badge tone={tone} title={label}>{tier} · {names[tier] ?? tier}</Badge>;
}

export function Hint({ text, children }: { text: string; children: ReactNode }) {
  if (!text) return <>{children}</>;
  return (
    <span className="tip cursor-help border-b border-dotted border-slate-400" tabIndex={0}>
      {children}
      <span className="tipbox">{text}</span>
    </span>
  );
}

export function EvidenceLink({ refs, count, title, label }: { refs: string[]; count?: number; title: string; label?: string }) {
  const { openEvidence } = useApp();
  const n = count ?? refs.length;
  if (!refs.length) return null;
  return (
    <button
      onClick={() => openEvidence(title, refs)}
      className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700 hover:bg-brand-soft hover:text-brand"
      title="Show the source rows behind this number"
    >
      <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M2 4h12M2 8h12M2 12h8" /></svg>
      {label ?? `${n} source row${n === 1 ? "" : "s"}`}
    </button>
  );
}

export function Stat({ fig, big = false, tone, hint }: { fig: Figure; big?: boolean; tone?: string; hint?: string }) {
  return (
    <div>
      <div className="text-sm text-muted"><Hint text={hint ?? fig.hint}>{fig.label}</Hint></div>
      <div className={`num font-semibold tracking-tight ${big ? "text-4xl" : "text-2xl"} ${tone ?? ""}`}>{money(fig.value, fig.currency)}</div>
      <div className="mt-1"><EvidenceLink refs={fig.evidence} count={fig.evidence_count} title={fig.label} /></div>
    </div>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="animate-pulse rounded-xl border border-line bg-white p-6 text-muted">{what}…</div>;
}

export function ErrorBox({ error, status }: { error: string; status?: number }) {
  return (
    <div className="rounded-xl border border-bad/30 bg-bad-soft p-4 text-sm text-bad">
      {status === 501 ? "This report is not available yet on the backend." : "Could not load this."}{" "}
      <span className="opacity-80">{error}</span>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-line bg-white p-8 text-center text-muted">{children}</div>;
}

export const SEV_TONE = { critical: "bad", warning: "warn", info: "info" } as const;
