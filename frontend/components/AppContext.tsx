"use client";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, get, getToken, ownerCall, setToken } from "@/lib/api";
import type { Company, EvidenceRow } from "@/lib/types";

interface EvidenceView { title: string; refs: string[] }
interface Ctx {
  companies: Company[];
  company: Company | null;
  companyId: string;
  setCompanyId: (id: string) => void;
  version: number;              // bumped after reset/approval so pages refetch
  bump: () => void;
  openEvidence: (title: string, refs: string[]) => void;
  evidence: EvidenceView | null;
  closeEvidence: () => void;
  /** Run an owner action; asks for the owner token if the backend demands one. */
  withOwner: <T>(fn: () => Promise<T>) => Promise<T>;
  tokenPrompt: boolean;
  submitToken: (t: string | null) => void;
  reload: () => void;
  notify: (msg: string, tone?: "good" | "bad") => void;
  toast: { msg: string; tone: "good" | "bad" } | null;
}

const AppCtx = createContext<Ctx | null>(null);
export const useApp = () => {
  const c = useContext(AppCtx);
  if (!c) throw new Error("AppProvider missing");
  return c;
};

const HERO = "cn_xborder";

export function AppProvider({ children }: { children: ReactNode }) {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCid] = useState<string>(HERO);
  const [version, setVersion] = useState(0);
  const [evidence, setEvidence] = useState<EvidenceView | null>(null);
  const [tokenPrompt, setTokenPrompt] = useState(false);
  const [toast, setToast] = useState<{ msg: string; tone: "good" | "bad" } | null>(null);
  const notify = useCallback((msg: string, tone: "good" | "bad" = "good") => {
    setToast({ msg, tone });
    setTimeout(() => setToast(null), 7000);
  }, []);
  const waiter = useRef<((t: string | null) => void) | null>(null);

  const reload = useCallback(() => {
    get<Company[]>("/companies").then(setCompanies).catch(() => setCompanies([]));
  }, []);
  useEffect(() => { reload(); }, [reload, version]);
  // The first Cash Truth run per company is slow on the backend (~30 s); warm the cache for all companies.
  const warmed = useRef<string>("");
  useEffect(() => {
    const key = companies.map((c) => c.id).join(",") + ":" + version;
    if (!companies.length || warmed.current === key) return;
    warmed.current = key;
    (async () => {
      for (const c of [...companies].sort((a) => (a.id === companyId ? -1 : 0))) {
        try { await get(`/reports/${c.id}/cash-truth`); } catch { /* ignore */ }
      }
    })();
  }, [companies, version, companyId]);
  useEffect(() => {
    try { const s = localStorage.getItem("copilot_company"); if (s) setCid(s); } catch { /* ignore */ }
  }, []);

  const setCompanyId = (id: string) => {
    setCid(id);
    try { localStorage.setItem("copilot_company", id); } catch { /* ignore */ }
  };

  const askToken = () => new Promise<string | null>((resolve) => {
    waiter.current = resolve;
    setTokenPrompt(true);
  });
  const submitToken = (t: string | null) => {
    setTokenPrompt(false);
    if (t) setToken(t);
    waiter.current?.(t);
    waiter.current = null;
  };

  const withOwner = async <T,>(fn: () => Promise<T>): Promise<T> => {
    try {
      return await fn();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setToken("");
        const t = await askToken();
        if (!t) throw new ApiError(401, "Owner token required to approve or reject.");
        return await fn();
      }
      throw e;
    }
  };

  const company = companies.find((c) => c.id === companyId) ?? null;

  return (
    <AppCtx.Provider
      value={{
        companies, company, companyId, setCompanyId, version, bump: () => setVersion((v) => v + 1),
        openEvidence: (title, refs) => setEvidence({ title, refs }), evidence,
        closeEvidence: () => setEvidence(null), withOwner, tokenPrompt, submitToken, reload, notify, toast,
      }}
    >
      {children}
    </AppCtx.Provider>
  );
}

/** Fetch JSON; refetches when the path or the app version changes. */
export function useApi<T>(path: string | null) {
  const { version } = useApp();
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean; status?: number }>({
    data: null, error: null, loading: !!path,
  });
  useEffect(() => {
    if (!path) { setState({ data: null, error: null, loading: false }); return; }
    let live = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    get<T>(path)
      .then((d) => live && setState({ data: d, error: null, loading: false }))
      .catch((e) => live && setState({ data: null, error: String(e.message || e), loading: false, status: e.status }));
    return () => { live = false; };
  }, [path, version]);
  return state;
}

export async function fetchEvidence(companyId: string, refs: string[]): Promise<{ rows: EvidenceRow[]; missing: string[] }> {
  const out: EvidenceRow[] = [];
  const missing: string[] = [];
  for (let i = 0; i < refs.length; i += 100) {
    const chunk = refs.slice(i, i + 100);
    const qs = chunk.map((r) => "ref=" + encodeURIComponent(r)).join("&");
    const r = await get<{ rows: EvidenceRow[]; missing: string[] }>(`/evidence?company_id=${companyId}&${qs}`);
    out.push(...r.rows); missing.push(...r.missing);
  }
  return { rows: out, missing };
}

export { getToken, ownerCall };
