"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { AppProvider, useApp } from "./AppContext";
import { EvidencePanel } from "./EvidencePanel";
import { TierBadge } from "./ui";
import { DEV_TOOLS, ownerCall } from "@/lib/api";
import type { Tier } from "@/lib/types";

const NAV = [
  { href: "/", label: "Owner Brief", icon: "M3 12l9-8 9 8M5 10v10h14V10" },
  { href: "/chat", label: "Ask the copilot", icon: "M4 5h16v11H9l-5 4V5z" },
  { href: "/cash-truth", label: "Cash Truth", icon: "M12 3v18M7 8c0-2 2-3 5-3s5 1 5 3-2 3-5 4-5 2-5 4 2 3 5 3 5-1 5-3" },
  { href: "/money", label: "Money & forecast", icon: "M4 19V9m6 10V5m6 14v-7m4 7H2" },
  { href: "/financing", label: "Financing", icon: "M3 10h18M5 10V7l7-4 7 4v3M6 10v8m4-8v8m4-8v8m4-8v8M3 21h18" },
  { href: "/approvals", label: "Approvals", icon: "M5 12l4 4 10-10" },
  { href: "/audit", label: "Audit log", icon: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z" },
  { href: "/connectors", label: "Connectors", icon: "M9 7V3m6 4V3M7 7h10v4a5 5 0 01-10 0V7zm5 9v5" },
];

function Icon({ d }: { d: string }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>;
}

function TokenModal() {
  const { tokenPrompt, submitToken } = useApp();
  const [v, setV] = useState("");
  if (!tokenPrompt) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-ink/40">
      <form
        className="w-[440px] rounded-2xl bg-white p-6 shadow-2xl"
        onSubmit={(e) => { e.preventDefault(); submitToken(v.trim() || null); setV(""); }}
      >
        <h2 className="text-lg font-semibold">Owner sign-off required</h2>
        <p className="mt-1 text-sm text-muted">
          Approving or rejecting changes is reserved for the business owner. Enter the owner token for this demo instance.
          It is kept only for this browser tab.
        </p>
        <input
          autoFocus type="password" value={v} onChange={(e) => setV(e.target.value)} placeholder="Owner token"
          className="mt-4 w-full rounded-lg border border-line px-3 py-2 outline-none focus:border-brand"
        />
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={() => { submitToken(null); setV(""); }} className="rounded-lg px-4 py-2 text-sm text-muted hover:bg-slate-100">Cancel</button>
          <button className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:opacity-90">Continue</button>
        </div>
      </form>
    </div>
  );
}

function Header() {
  const { companies, company, companyId, setCompanyId, bump, reload, withOwner } = useApp();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const setTier = async (tier: Tier) => {
    try {
      await withOwner(() => ownerCall(`/companies/${companyId}/permissions`, { tier }, "PUT"));
      reload(); bump();
    } catch (e) { setMsg(String((e as Error).message)); }
  };
  const reset = async () => {
    if (!confirm("Wipe and reload the demo data? (dev only)")) return;
    setBusy(true); setMsg("");
    try {
      await withOwner(() => ownerCall("/admin/reset"));
      bump(); setMsg("Demo data reloaded");
    } catch (e) { setMsg(String((e as Error).message)); }
    setBusy(false);
  };

  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-line bg-white px-8 py-3">
      <label className="flex items-center gap-2 text-sm text-muted">
        Business
        <select
          value={companyId} onChange={(e) => setCompanyId(e.target.value)}
          className="rounded-lg border border-line bg-white px-3 py-1.5 text-[15px] font-medium text-ink"
        >
          {companies.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.base_currency}){c.id === "cn_xborder" ? " ★" : ""}</option>)}
        </select>
      </label>
      {company && (
        <label className="flex items-center gap-2 text-sm text-muted">
          Agent permission
          <select
            value={company.granted_tier} onChange={(e) => setTier(e.target.value as Tier)}
            className="rounded-lg border border-line bg-white px-2 py-1.5 text-sm text-ink" title="What the copilot is allowed to do for this business"
          >
            <option value="T0">T0 · Read</option><option value="T1">T1 · Analyse / suggest</option>
            <option value="T2">T2 · Draft (needs approval)</option><option value="T3">T3 · Limited action</option>
          </select>
          <TierBadge tier={company.granted_tier} label={company.tier_label} />
        </label>
      )}
      <div className="ml-auto flex items-center gap-3">
        {msg && <span className="text-sm text-muted">{msg}</span>}
        {DEV_TOOLS && (
          <button onClick={reset} disabled={busy} className="rounded-lg border border-line px-3 py-1.5 text-sm text-muted hover:bg-slate-50 disabled:opacity-50" title="Development only">
            {busy ? "Resetting…" : "Reset demo"}
          </button>
        )}
      </div>
    </header>
  );
}

function Toast() {
  const { toast } = useApp();
  if (!toast) return null;
  return <div role="status" className={`fixed bottom-6 right-6 z-[70] max-w-sm rounded-xl px-5 py-3 text-sm font-medium text-white shadow-xl ${toast.tone === "good" ? "bg-good" : "bg-bad"}`}>{toast.msg}</div>;
}

function Frame({ children }: { children: ReactNode }) {
  const path = usePathname();
  return (
    <div className="flex min-h-screen">
      <nav className="sticky top-0 flex h-screen w-64 shrink-0 flex-col border-r border-line bg-ink px-3 py-5 text-slate-200">
        <div className="mb-6 px-3">
          <div className="text-lg font-semibold tracking-tight text-white">Finance Copilot</div>
          <div className="text-xs text-slate-400">AI as a financial participant</div>
        </div>
        <ul className="space-y-1">
          {NAV.map((n) => {
            const on = n.href === "/" ? path === "/" : path.startsWith(n.href);
            return (
              <li key={n.href}>
                <Link href={n.href} className={`flex items-center gap-3 rounded-lg px-3 py-2 text-[15px] ${on ? "bg-white/10 text-white" : "text-slate-300 hover:bg-white/5"}`}>
                  <Icon d={n.icon} />{n.label}
                </Link>
              </li>
            );
          })}
        </ul>
        <div className="mt-auto rounded-lg bg-white/5 p-3 text-xs leading-relaxed text-slate-400">
          Every number comes from a checked calculation, not from the AI. The copilot never moves money.
        </div>
      </nav>
      <div className="min-w-0 flex-1">
        <Header />
        <main className="mx-auto max-w-[1280px] px-8 py-7">{children}</main>
      </div>
      <EvidencePanel />
      <TokenModal />
      <Toast />
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  return <AppProvider><Frame>{children}</Frame></AppProvider>;
}
