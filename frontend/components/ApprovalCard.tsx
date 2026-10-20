"use client";
import { useState } from "react";
import { useApp } from "./AppContext";
import { Badge, EvidenceLink, TierBadge } from "./ui";
import { ownerCall } from "@/lib/api";
import { dateTime, money } from "@/lib/format";
import type { Approval } from "@/lib/types";

interface Payload { args?: { entry_date?: string; memo?: string; reason?: string; lines?: { account_id: string; debit: number; credit: number; memo?: string }[]; evidence?: string[] } }

export function ApprovalCard({ a, currency, onDone }: { a: Approval; currency?: string; onDone?: () => void }) {
  const { withOwner, bump, notify } = useApp();
  const [busy, setBusy] = useState<"" | "approve" | "reject">("");
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const args = ((a.payload as Payload | undefined)?.args) ?? {};
  const status = a.status;
  const tone = status === "pending" ? "warn" : status === "approved" ? "good" : status === "rejected" ? "neutral" : "bad";

  const decide = async (what: "approve" | "reject") => {
    setBusy(what); setErr("");
    try {
      await withOwner(() => ownerCall(`/approvals/${a.id}/${what}`, { note: what === "reject" ? "Rejected in the web app" : "Approved in the web app" }));
      const m = what === "approve" ? "Approved — saved as a draft entry. See it in the audit log." : "Rejected — nothing was written.";
      setNote(m); notify(m);
      bump(); onDone?.();
    } catch (e) { setErr(String((e as Error).message)); }
    setBusy("");
  };

  return (
    <div className="rounded-xl border border-line bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="font-semibold">{String(a.title ?? a.tool)}</div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
            <TierBadge tier={a.tier} />
            <Badge tone={tone}>{status}</Badge>
            <span>requested by {String(a.requested_by)} · {dateTime(String(a.requested_at))}</span>
          </div>
        </div>
        {args.evidence && args.evidence.length > 0 && <EvidenceLink refs={args.evidence} title={String(a.title ?? "Draft evidence")} label="Why: source rows" />}
      </div>
      {args.reason && <p className="mt-3 text-[15px]">{args.reason}</p>}
      {args.lines && (
        <table className="mt-3 w-full text-sm">
          <thead><tr className="text-left text-xs uppercase tracking-wide text-muted"><th className="py-1">Account</th><th className="text-right">Debit</th><th className="text-right">Credit</th></tr></thead>
          <tbody>
            {args.lines.map((l, i) => (
              <tr key={i} className="border-t border-line">
                <td className="py-1.5 font-mono text-xs">{l.account_id}</td>
                <td className="num text-right">{l.debit ? money(l.debit, currency) : ""}</td>
                <td className="num text-right">{l.credit ? money(l.credit, currency) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {args.entry_date && <p className="mt-2 text-xs text-muted">Entry date {args.entry_date}. Saved as a draft only — never posted automatically. No money moves.</p>}
      {status === "pending" && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button disabled={!!busy} onClick={() => decide("approve")} className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50">
            {busy === "approve" ? "Approving…" : "Approve draft"}
          </button>
          <button disabled={!!busy} onClick={() => decide("reject")} className="rounded-lg border border-line px-4 py-2 text-sm font-semibold hover:bg-slate-50 disabled:opacity-50">
            {busy === "reject" ? "Rejecting…" : "Reject"}
          </button>
          {err && <span className="text-sm text-bad">{err}</span>}
        </div>
      )}
      {status !== "pending" && a.decided_by ? <p className="mt-2 text-xs text-muted">Decided by {String(a.decided_by)} · {dateTime(String(a.decided_at))}{a.decision_note ? ` · ${String(a.decision_note)}` : ""}</p> : null}
      {note && <p className="mt-2 text-sm text-good">{note}</p>}
    </div>
  );
}
