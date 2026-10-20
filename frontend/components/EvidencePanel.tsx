"use client";
import { useEffect, useState } from "react";
import { fetchEvidence, useApp } from "./AppContext";
import type { EvidenceRow } from "@/lib/types";

const HIDE = new Set(["company_id", "ingested_at", "ingest_batch_id", "entry_id"]);

function show(v: unknown) {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(v);
  return String(v);
}

export function EvidencePanel() {
  const { evidence, closeEvidence, companyId } = useApp();
  const [rows, setRows] = useState<EvidenceRow[]>([]);
  const [missing, setMissing] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [limit, setLimit] = useState(25);

  useEffect(() => {
    if (!evidence) return;
    let live = true;
    setBusy(true); setErr(""); setRows([]); setLimit(25);
    fetchEvidence(companyId, evidence.refs.slice(0, 200))
      .then((r) => { if (live) { setRows(r.rows); setMissing(r.missing); } })
      .catch((e) => live && setErr(String(e.message || e)))
      .finally(() => live && setBusy(false));
    return () => { live = false; };
  }, [evidence, companyId]);

  if (!evidence) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-ink/30" onClick={closeEvidence}>
      <aside className="flex h-full w-[560px] max-w-full flex-col bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <header className="flex items-start justify-between gap-3 border-b border-line p-5">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-brand">Evidence</div>
            <h2 className="text-lg font-semibold">{evidence.title}</h2>
            <p className="text-sm text-muted">{evidence.refs.length} source row{evidence.refs.length === 1 ? "" : "s"} · original records with where they came from</p>
          </div>
          <button onClick={closeEvidence} className="rounded-md px-2 py-1 text-xl leading-none text-muted hover:bg-slate-100" aria-label="Close">×</button>
        </header>
        <div className="flex-1 space-y-3 overflow-y-auto p-5">
          {busy && <div className="text-muted">Loading source rows…</div>}
          {err && <div className="text-bad">{err}</div>}
          {rows.slice(0, limit).map((r) => (
            <div key={r.ref} className="rounded-xl border border-line bg-canvas p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="rounded bg-white px-2 py-0.5 font-mono text-xs text-slate-700">{r.ref}</span>
                <span className="text-xs text-muted">{String(r.row.source_system ?? "")} {r.row.source_file ? "· " + r.row.source_file : ""}</span>
              </div>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm">
                {Object.entries(r.row).filter(([k, v]) => !HIDE.has(k) && v !== null && v !== "").map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted">{k.replace(/_/g, " ")}</dt>
                    <dd className="num break-all">{show(v)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
          {rows.length > limit && (
            <button onClick={() => setLimit(limit + 25)} className="w-full rounded-lg border border-line py-2 text-sm font-medium hover:bg-slate-50">
              Show 25 more ({rows.length - limit} left)
            </button>
          )}
          {evidence.refs.length > 200 && <div className="text-xs text-muted">Showing the first 200 of {evidence.refs.length} references.</div>}
          {missing.length > 0 && <div className="text-xs text-warn">{missing.length} reference(s) could not be resolved.</div>}
        </div>
      </aside>
    </div>
  );
}
