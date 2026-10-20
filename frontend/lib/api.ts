export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8765").replace(/\/$/, "");
export const DEV_TOOLS = process.env.NEXT_PUBLIC_DEV_TOOLS !== "0";

const TOKEN_KEY = "copilot_owner_token";
export function getToken(): string {
  try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
export function setToken(t: string) {
  try { if (t) sessionStorage.setItem(TOKEN_KEY, t); else sessionStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function parse(res: Response) {
  const text = await res.text();
  let body: unknown = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!res.ok) {
    const d = (body as { detail?: unknown } | null)?.detail;
    throw new ApiError(res.status, typeof d === "string" ? d : `HTTP ${res.status}`);
  }
  return body;
}

export async function get<T>(path: string): Promise<T> {
  return (await parse(await fetch(API_BASE + path, { cache: "no-store" }))) as T;
}

/** Owner-only call. Sends the bearer token when one is stored. */
export async function ownerCall<T>(path: string, body?: unknown, method = "POST"): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", "X-Actor": "owner" };
  const t = getToken();
  if (t) headers.Authorization = `Bearer ${t}`;
  const res = await fetch(API_BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  return (await parse(res)) as T;
}

export const q = encodeURIComponent;
