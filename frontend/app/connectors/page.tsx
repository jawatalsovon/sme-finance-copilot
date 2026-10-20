"use client";
import { useApi } from "@/components/AppContext";
import { Badge, Card, Empty, ErrorBox, Loading, PageTitle } from "@/components/ui";

interface Meta {
  id: string; name: string; category: string; auth_type: string; status: "live" | "file" | "sandbox" | "planned";
  description: string; countries: string[]; canonical_tables: string[]; supports_write_draft: boolean; docs_url: string; modes: string[];
}
const TONE = { live: "good", file: "info", sandbox: "warn", planned: "neutral" } as const;
const ORDER = { live: 0, sandbox: 1, file: 2, planned: 3 } as const;
const AUTH: Record<string, string> = { none: "no sign-in", file_upload: "file upload", oauth2: "secure sign-in (OAuth)", api_key: "API key", service_account: "service account" };

export default function ConnectorsPage() {
  const { data, error, loading } = useApi<Meta[]>("/connectors");
  const list = [...(data ?? [])].sort((a, b) => ORDER[a.status] - ORDER[b.status] || a.name.localeCompare(b.name));
  const planned = list.filter((c) => c.status === "planned");
  const active = list.filter((c) => c.status !== "planned");
  return (
    <>
      <PageTitle title="Connectors" sub="Where the copilot reads your numbers from. Nothing here can move money; writes go to a sandbox or a draft and need your approval." />
      {loading && <Loading what="Loading connectors" />}
      {error && <ErrorBox error={error} />}
      {data && active.length === 0 && <Empty>No connectors are registered yet.</Empty>}
      <div className="grid gap-4 md:grid-cols-2">
        {active.map((c) => (
          <Card key={c.id}>
            <div className="flex items-start justify-between gap-2">
              <h3 className="text-lg font-semibold">{c.name}</h3>
              <Badge tone={TONE[c.status]}>{c.status}</Badge>
            </div>
            <p className="mt-1 text-sm text-muted">{c.description}</p>
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <Badge tone="neutral">{c.category}</Badge>
              <Badge tone="neutral">{AUTH[c.auth_type] ?? c.auth_type}</Badge>
              {c.supports_write_draft && <Badge tone="warn">can write drafts (needs approval)</Badge>}
              {c.countries.length > 0 && <Badge tone="neutral">{c.countries.join(", ")}</Badge>}
            </div>
            {c.canonical_tables.length > 0 && <p className="mt-2 text-xs text-muted">Brings in: {c.canonical_tables.join(", ")}</p>}
            {c.docs_url && <a href={c.docs_url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-sm text-brand hover:underline">Docs ↗</a>}
          </Card>
        ))}
      </div>
      <h2 id="roadmap" className="mb-2 mt-8 text-xl font-semibold">Roadmap</h2>
      <p className="mb-3 text-sm text-muted">Honest list of what is not built yet. Full details live in <code className="rounded bg-slate-100 px-1">docs/CONNECTOR_ROADMAP.md</code>.</p>
      {planned.length === 0
        ? <Empty>Planned connectors are listed in docs/CONNECTOR_ROADMAP.md in the project repository.</Empty>
        : <div className="flex flex-wrap gap-2">{planned.map((c) => <Badge key={c.id} tone="neutral" title={c.description}>{c.name}</Badge>)}</div>}
    </>
  );
}
