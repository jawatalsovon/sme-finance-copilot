"use client";
import { useState } from "react";
import { useApp, useApi } from "@/components/AppContext";
import { ApprovalCard } from "@/components/ApprovalCard";
import { Card, Empty, ErrorBox, Loading, PageTitle } from "@/components/ui";
import type { Approval } from "@/lib/types";

const TABS = [["pending", "Waiting for you"], ["approved", "Approved"], ["rejected", "Rejected"], ["", "All"]] as const;

export default function ApprovalsPage() {
  const { companyId, company } = useApp();
  const [tab, setTab] = useState<string>("pending");
  const { data, error, loading } = useApi<Approval[]>(`/approvals?company_id=${companyId}${tab ? `&status=${tab}` : ""}`);
  return (
    <>
      <PageTitle title="Approvals" sub="Anything the copilot wants to write goes here first. It stays a draft until you approve it. No approval ever moves money." />
      <div className="mb-4 flex gap-2">
        {TABS.map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className={`rounded-full px-4 py-1.5 text-sm font-medium ${tab === k ? "bg-ink text-white" : "border border-line bg-white text-muted hover:bg-slate-50"}`}>{l}</button>
        ))}
      </div>
      {loading && <Loading what="Loading approvals" />}
      {error && <ErrorBox error={error} />}
      {data && data.length === 0 && <Empty>Nothing here. When the copilot proposes a correction, it will appear for your decision.</Empty>}
      <div className="space-y-4">
        {data?.map((a) => <ApprovalCard key={a.id} a={a} currency={company?.base_currency} />)}
      </div>
      <Card className="mt-6 text-sm text-muted">Approving saves the entry as a <b>draft</b> in the books. A person still reviews and posts it in the accounting system. The decision is written to the tamper-evident audit log.</Card>
    </>
  );
}
