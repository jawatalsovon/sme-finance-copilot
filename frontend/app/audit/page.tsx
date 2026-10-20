"use client";
import { Fragment, useState } from "react";
import { useApp, useApi } from "@/components/AppContext";
import { Badge, Card, ErrorBox, Loading, PageTitle, TierBadge } from "@/components/ui";
import { get } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { AuditRow } from "@/lib/types";

interface Verify { ok: boolean; errors: unknown[]; [k: string]: unknown }

function pretty(action: string): string {
  return action.replace(/_/g, " ");
}
function brief(payload: string): string {
  try {
    const p = JSON.parse(payload);
    const keep = ["approval_id", "title", "memo", "decision", "reason", "error", "summary", "status", "approver", "tool", "path"];
    const bits = keep.filter((k) => p[k] != null).map((k) => `${k}: ${typeof p[k] === "object" ? JSON.stringify(p[k]).slice(0, 60) : String(p[k]).slice(0, 80)}`);
    return bits.length ? bits.join(" · ") : JSON.stringify(p).slice(0, 120);
  } catch { return payload.slice(0, 120); }
}

export default function AuditPage() {
  const { companyId, bump } = useApp();
  const [scope, setScope] = useState<"company" | "all">("company");
  const { data, error, loading } = useApi<AuditRow[]>(`/audit?limit=200${scope === "company" ? `&company_id=${companyId}` : ""}`);
  const [ver, setVer] = useState<Verify | null>(null);
  const [vbusy, setVbusy] = useState(false);
  const [open, setOpen] = useState<number | null>(null);

  const verify = async () => {
    setVbusy(true);
    try { setVer(await get<Verify>("/audit/verify")); } catch { setVer({ ok: false, errors: ["Could not reach the audit service"] }); }
    setVbusy(false);
  };

  return (
    <>
      <PageTitle
        title="Audit log"
        sub="Every action the copilot takes, and every decision you make, in a chain where each entry locks in the one before it. Edits or deletions break the chain."
        right={
          <div className="flex items-center gap-3">
            {ver && (
              <span className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${ver.ok ? "bg-good-soft text-good" : "bg-bad-soft text-bad"}`}>
                {ver.ok ? "✓ Chain intact — no tampering found" : `✕ Chain broken (${ver.errors.length} problem${ver.errors.length === 1 ? "" : "s"})`}
              </span>
            )}
            <button onClick={verify} disabled={vbusy} className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">{vbusy ? "Checking…" : "Verify chain"}</button>
          </div>
        }
      />
      <div className="mb-3 flex gap-2">
        {(["company", "all"] as const).map((s) => (
          <button key={s} onClick={() => setScope(s)} className={`rounded-full px-4 py-1.5 text-sm font-medium ${scope === s ? "bg-ink text-white" : "border border-line bg-white text-muted"}`}>{s === "company" ? "This business" : "Everything"}</button>
        ))}
        <button onClick={bump} className="ml-auto text-sm text-brand hover:underline">Refresh</button>
      </div>
      {loading && <Loading what="Loading the log" />}
      {error && <ErrorBox error={error} />}
      {data && (
        <Card className="!p-0 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-canvas text-left text-xs uppercase tracking-wide text-muted"><tr><th className="px-4 py-2">#</th><th>When</th><th>Who</th><th>What</th><th>Tier</th><th>Detail</th></tr></thead>
            <tbody>
              {data.map((r) => (
                <Fragment key={r.seq}>
                  <tr onClick={() => setOpen(open === r.seq ? null : r.seq)} className="cursor-pointer border-t border-line hover:bg-slate-50">
                    <td className="num px-4 py-2 text-muted">{r.seq}</td>
                    <td className="whitespace-nowrap">{dateTime(r.ts)}</td>
                    <td><Badge tone={r.actor === "owner" ? "brand" : r.actor === "agent" ? "info" : "neutral"}>{r.actor}</Badge></td>
                    <td className="font-medium">{pretty(r.action)}{r.tool ? <span className="text-muted"> · {r.tool}</span> : null}</td>
                    <td>{r.tier ? <TierBadge tier={r.tier} /> : null}</td>
                    <td className="max-w-[420px] truncate text-muted">{brief(r.payload_json)}</td>
                  </tr>
                  {open === r.seq && (
                    <tr className="bg-canvas"><td colSpan={6} className="px-4 py-3">
                      <pre className="whitespace-pre-wrap break-all font-mono text-xs">{JSON.stringify(JSON.parse(r.payload_json || "{}"), null, 2)}</pre>
                      <div className="mt-2 font-mono text-[11px] text-muted">prev {r.prev_hash.slice(0, 16)}… → this {r.row_hash.slice(0, 16)}…</div>
                    </td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
