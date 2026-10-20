# SME Finance Copilot — frontend

Owner-facing web app (Next.js 15 App Router, React 19, TypeScript).
UI stack: **Tailwind CSS 4** + **Recharts 2** (forecast bands). No other UI dependencies; system fonts only.

## Run
```bash
cd frontend
npm install
cp .env.example .env.local      # optional; defaults to http://localhost:8765
npm run dev                     # http://localhost:3000   (backend CORS allows this origin)
npm run build && npm run start  # production check
```
Backend (repo root, mock LLM): `ADMIN_RESET_ENABLED=1 .venv/Scripts/python -m uvicorn backend.api.main:app --port 8765`,
then press **Reset demo** (or `POST /admin/reset`) to load the three demo businesses.
Needs Node 20+ (tested on Node 24, npm 11). Use `typescript@5.x` (Next 15 does not support TS 7).

## Screens
| Route | What it shows |
|---|---|
| `/` Owner Brief | Books vs real cash gap, top alerts, KPIs, pending approvals (Approve/Reject) and suggested drafts. EN / 中文 / বাংলা toggle is wired but disabled until the backend sends `summaries` |
| `/chat` | SSE chat: tool-step chips with tier badges, refusals, typewriter answer, numbers link to evidence |
| `/cash-truth` | Book vs verified by account, findings grouped by cause with confidence, evidence and proposed draft entry |
| `/money` | KPIs, monthly P&L in plain words, 13-week / 12-month forecast with 80 % and 95 % bands, alerts |
| `/financing` | Matched products, pass/fail reasons, affordability, "illustrative" labels |
| `/approvals`, `/audit` | Approval queue; audit table with **Verify chain** (green/red) |
| `/connectors` | Registry from `GET /connectors` + roadmap section |

Header: company switcher (hero `cn_xborder` default), agent permission tier selector (`PUT /companies/{id}/permissions`), dev-only **Reset demo** (hide with `NEXT_PUBLIC_DEV_TOOLS=0`).
Owner actions send `Authorization: Bearer <token>` only after the backend answers 401; the token prompt keeps it in `sessionStorage`.

## Notes
- Owner Brief tries `GET /reports/{id}/owner-brief`; on 404/501 it assembles the same facts from cash-truth, alerts, kpis and forecast.
- The first Cash Truth call per company takes ~30 s on the backend; the app warms all companies on load.
- Screenshots (1920x1080) are in `screenshots/`.
