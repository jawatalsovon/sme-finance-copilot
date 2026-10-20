"""FastAPI app.  Run:  uvicorn backend.api.main:app --port 8000

Endpoints (JSON unless noted)
  GET  /health                                   liveness + provider/db status
  GET  /companies                                list companies
  GET  /companies/{id}/permissions               granted tier
  PUT  /companies/{id}/permissions   [owner]     {"tier": "T0".."T3"}
  GET  /tools?company_id=                        tools with tier and whether the company may use them
  POST /chat                                     SSE stream of agent events (see backend/agent/loop.py)
  POST /chat/sync                                same turn as one JSON object (scripted tasks / judges)
  POST /admin/reset                  [owner]     wipe + reload seeded demo state (ADMIN_RESET_ENABLED=1, localhost)
  GET  /approvals?company_id=&status=            approval queue
  POST /approvals/{id}/approve       [owner]     run the queued T2 draft action
  POST /approvals/{id}/reject        [owner]
  GET  /audit?company_id=&action=&limit=         audit rows (newest first)
  GET  /audit/verify                             hash-chain check -> {"ok": bool, "errors": [...]}
  GET  /connectors                               connector registry (live / sandbox / planned)
  GET  /evidence?company_id=&ref=...             source rows + lineage behind evidence refs
  GET  /usage                                    LLM spend today vs cap
  GET  /reports/{company_id}/cash-truth|pnl|kpis|forecast|alerts|financing ; POST .../scenario
       -> engine results, or 501 until M1 implements the engine function

[owner] endpoints require "Authorization: Bearer <OWNER_API_TOKEN>" when that variable is set.
The actor recorded in the audit log comes from the X-Actor header (default "owner").
"""
from __future__ import annotations

import datetime as dt
import hmac
from contextlib import asynccontextmanager
from typing import Any, Iterator, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend import __version__
from backend.agent.executor import SelfApprovalForbidden
from backend.api.container import Container, build_container, reset_state
from backend.config import Settings, get_settings
from backend.core.util import business_today, to_json, utc_now
from backend.engine.interfaces import EngineError, EngineNotImplemented, get_engine
from backend.ledger import queries
from backend.ledger.lineage import get_source_rows
from backend.security.approvals import ApprovalError
from backend.security.tiers import TIER_LABELS, Tier


class ChatRequest(BaseModel):
    company_id: str
    message: str = Field(min_length=1, max_length=4000)
    session_id: Optional[str] = None
    seed: Optional[int | str] = None            # deterministic ids (+ mock answers) for scripted runs


class ResetRequest(BaseModel):
    seed: Optional[int | str] = None


class TierRequest(BaseModel):
    tier: Literal["T0", "T1", "T2", "T3"]


class DecisionRequest(BaseModel):
    note: str = ""


class ScenarioRequest(BaseModel):
    changes: list[dict[str, Any]]
    as_of: Optional[dt.date] = None


def summarize_events(events: list[dict], include_events: bool = False) -> dict:
    """Collapse one turn's events into the /chat/sync response."""
    final = next((e for e in events if e["type"] == "final"), None)
    error = next((e for e in events if e["type"] == "error"), None)
    session = next((e for e in events if e["type"] == "session"), {})
    calls = [e for e in events if e.get("kind") == "tool_call"]
    results = [e for e in events if e.get("kind") == "tool_result"]
    tool_calls = []
    for call, res in zip(calls, results):
        tool_calls.append({"tool": call["tool"], "tier": call["tier"], "args": call["args"],
                           "status": res["status"], "summary": res["summary"], "error": res["error"],
                           "approval_id": res["approval_id"], "evidence_count": res["evidence_count"]})
    out = {
        "session_id": session.get("session_id"),
        "company_id": session.get("company_id"),
        "granted_tier": session.get("tier"),
        "provider": session.get("provider"),
        "answer": final["answer"] if final else None,
        "evidence_ids": final["evidence_ids"] if final else [],
        "invalid_evidence_ids": final["invalid_evidence_ids"] if final else [],
        "unverified_numbers": final["unverified_numbers"] if final else [],
        "approvals": final["approvals"] if final else [],
        "refusals": final.get("refusals", []) if final else [],
        "tool_calls": tool_calls,
        "tier_decisions": [r["tier_decision"] for r in results],
        "guard_flags": [f | {"tool": e.get("tool"), "source": e.get("source", "tool_result")}
                        for e in events if e.get("kind") == "guard" for f in e["flags"]],
        "steps": final["steps"] if final else None,
        "error": {"code": error["code"], "message": error["message"]} if error else None,
    }
    if include_events:
        out["events"] = events
    return out


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or (container.settings if container else get_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "container", None) is None:
            app.state.container = build_container(settings)
        yield

    app = FastAPI(title="SME Finance Copilot API", version=__version__, lifespan=lifespan)
    app.state.container = container
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "OPTIONS"], allow_headers=["*"])

    def c(request: Request) -> Container:
        return request.app.state.container

    def actor_of(x_actor: Optional[str] = Header(default=None)) -> str:
        return (x_actor or "owner").strip()[:64] or "owner"

    def require_owner(request: Request, authorization: Optional[str] = Header(default=None),
                      x_actor: Optional[str] = Header(default=None)) -> None:
        """Owner-only actions: approve/reject, tier changes, /admin/reset.

        dev:        checked only when OWNER_API_TOKEN is set.
        demo/prod:  fail closed - a bearer token is mandatory, and with no token configured
                    nobody can perform owner actions. X-Actor is advisory (recorded, never trusted).
        """
        ct = c(request)
        token = ct.settings.owner_api_token
        if not token and not ct.settings.owner_auth_required:
            return
        supplied = (authorization or "").strip()
        bearer = supplied[7:].strip() if supplied.lower().startswith("bearer ") else ""
        if not token:
            reason = "not_configured"
        elif not bearer:
            reason = "missing"
        elif not hmac.compare_digest(bearer.encode(), token.encode()):
            reason = "wrong"
        else:
            return
        ct.audit.append("owner_auth_failed", actor="api",
                        payload={"path": request.url.path, "method": request.method, "reason": reason,
                                 "app_env": ct.settings.app_env, "claimed_actor": (x_actor or "")[:64]})
        detail = ("owner actions are disabled: OWNER_API_TOKEN is not configured for this instance"
                  if reason == "not_configured" else "owner token required (Authorization: Bearer <token>)")
        raise HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})

    def company_or_404(ct: Container, company_id: str) -> dict:
        comp = queries.get_company(ct.db, company_id)
        if not comp:
            raise HTTPException(404, f"unknown company {company_id!r}")
        return comp

    # ------------------------------------------------------------ basics
    @app.get("/health")
    def health(request: Request):
        ct = c(request)
        try:
            ct.db.scalar("SELECT 1")
            db_ok = "ok"
        except Exception as e:  # pragma: no cover
            db_ok = f"error: {type(e).__name__}"
        return {"status": "ok", "version": __version__, "db": db_ok, "provider": ct.provider.name,
                "model": ct.provider.model, "app_env": ct.settings.app_env,
                "owner_auth": "required" if (ct.settings.owner_auth_required or ct.settings.owner_api_token)
                else "off (dev)", "time": utc_now().isoformat()}

    @app.get("/companies")
    def companies(request: Request):
        ct = c(request)
        out = []
        for comp in queries.list_companies(ct.db):
            tier = ct.permissions.get_tier(comp["id"])
            out.append({**comp, "granted_tier": tier.value, "tier_label": TIER_LABELS[tier]})
        return out

    @app.get("/companies/{company_id}/permissions")
    def get_permissions(company_id: str, request: Request):
        ct = c(request)
        company_or_404(ct, company_id)
        tier = ct.permissions.get_tier(company_id)
        return {"company_id": company_id, "granted_tier": tier.value, "tier_label": TIER_LABELS[tier],
                "tiers": {t.value: TIER_LABELS[t] for t in Tier}}

    @app.put("/companies/{company_id}/permissions", dependencies=[Depends(require_owner)])
    def set_permissions(company_id: str, body: TierRequest, request: Request, actor: str = Depends(actor_of)):
        ct = c(request)
        company_or_404(ct, company_id)
        tier = ct.permissions.set_tier(company_id, body.tier, actor=actor)
        return {"company_id": company_id, "granted_tier": tier.value, "tier_label": TIER_LABELS[tier]}

    @app.get("/tools")
    def tools(request: Request, company_id: Optional[str] = None):
        ct = c(request)
        granted = ct.permissions.get_tier(company_id) if company_id else None
        return [{"name": t.name, "tier": t.tier.value, "description": t.description,
                 "allowed": granted.allows(t.tier) if granted else None,
                 "needs_approval": t.tier == Tier.T2} for t in ct.tools.all()]

    # ------------------------------------------------------------ chat (SSE)
    @app.post("/chat")
    def chat(body: ChatRequest, request: Request, actor: str = Depends(actor_of)):
        ct = c(request)
        company_or_404(ct, body.company_id)

        def stream() -> Iterator[str]:
            try:
                for event in ct.agent.run(body.company_id, body.message, body.session_id, actor=actor,
                                          seed=body.seed):
                    yield f"event: {event['type']}\ndata: {to_json(event)}\n\n"
            except Exception as e:  # never leak a traceback into the stream
                ct.audit.append("chat_error", actor="system", company_id=body.company_id,
                                payload={"error": f"{type(e).__name__}: {e}"})
                err = {"type": "error", "code": "internal_error", "message": f"{type(e).__name__}"}
                yield f"event: error\ndata: {to_json(err)}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/chat/sync")
    def chat_sync(body: ChatRequest, request: Request, actor: str = Depends(actor_of),
                  include_events: bool = False):
        """Non-streaming chat for scripted tasks: one JSON object per turn."""
        ct = c(request)
        company_or_404(ct, body.company_id)
        events = list(ct.agent.run(body.company_id, body.message, body.session_id, actor=actor, seed=body.seed))
        return summarize_events(events, include_events)

    # ------------------------------------------------------------ approvals
    @app.get("/approvals")
    def list_approvals(request: Request, company_id: Optional[str] = None,
                       status: Optional[Literal["pending", "approved", "rejected", "failed"]] = None,
                       limit: int = Query(100, le=500)):
        return c(request).approvals.list(company_id, status, limit)

    @app.post("/approvals/{approval_id}/approve", dependencies=[Depends(require_owner)])
    def approve(approval_id: str, request: Request, body: DecisionRequest | None = None,
                actor: str = Depends(actor_of)):
        try:
            return c(request).executor.execute_approved(approval_id, approver=actor)
        except SelfApprovalForbidden as e:
            forbid_agent_decision(request, approval_id, actor, str(e))
        except ApprovalError as e:
            raise HTTPException(409, str(e))

    @app.post("/approvals/{approval_id}/reject", dependencies=[Depends(require_owner)])
    def reject(approval_id: str, request: Request, body: DecisionRequest | None = None,
               actor: str = Depends(actor_of)):
        try:
            return c(request).executor.reject(approval_id, approver=actor, note=(body.note if body else ""))
        except SelfApprovalForbidden as e:
            forbid_agent_decision(request, approval_id, actor, str(e))
        except ApprovalError as e:
            raise HTTPException(409, str(e))

    def forbid_agent_decision(request: Request, approval_id: str, actor: str, reason: str):
        c(request).audit.append("self_approval_refused", actor=actor, payload={"approval_id": approval_id,
                                                                              "reason": reason})
        raise HTTPException(403, reason)

    # ------------------------------------------------------------ admin (test harness)
    @app.post("/admin/reset", dependencies=[Depends(require_owner)])
    def admin_reset(request: Request, body: ResetRequest | None = None):
        """Wipe the database and reload the seeded demo state. Local/dev only:
        needs ADMIN_RESET_ENABLED=1 and a request from this machine."""
        ct = c(request)
        if not ct.settings.admin_reset_enabled:
            raise HTTPException(403, "admin reset is disabled (set ADMIN_RESET_ENABLED=1 for local test runs)")
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            ct.audit.append("admin_reset_refused", actor="api", payload={"host": host})
            raise HTTPException(403, "admin reset is only allowed from localhost")
        return reset_state(ct, seed=body.seed if body else None)

    # ------------------------------------------------------------ audit
    @app.get("/audit")
    def audit(request: Request, company_id: Optional[str] = None, action: Optional[str] = None,
              limit: int = Query(100, le=1000), offset: int = 0):
        return c(request).audit.list(company_id, limit, offset, action)

    @app.get("/audit/verify")
    def audit_verify(request: Request):
        return c(request).audit.verify_chain().as_dict()

    # ------------------------------------------------------------ connectors / evidence / usage
    @app.get("/connectors")
    def connectors(request: Request):
        return [m.model_dump() for m in c(request).connectors.list_metadata()]

    @app.get("/evidence")
    def evidence(request: Request, company_id: str, ref: list[str] = Query(default=[])):
        ct = c(request)
        company_or_404(ct, company_id)
        if len(ref) > 200:
            raise HTTPException(422, "at most 200 refs per request")
        return get_source_rows(ct.db, ref, company_id=company_id)

    @app.get("/usage")
    def usage(request: Request):
        return c(request).cost_guard.status()

    # ------------------------------------------------------------ reports (engine)
    def run_engine(request: Request, company_id: str, method: str, **kwargs):
        ct = c(request)
        company_or_404(ct, company_id)
        factory = ct.executor.engine_factory or get_engine
        try:
            result = getattr(factory(ct.db), method)(company_id, **kwargs)
        except EngineNotImplemented as e:
            return JSONResponse(status_code=501, content={"detail": str(e), "code": "not_implemented",
                                                          "function": method, "owner": "M1"})
        except EngineError as e:
            raise HTTPException(422, str(e))
        return result.model_dump(mode="json")

    today = business_today

    @app.get("/reports/{company_id}/cash-truth")
    def r_cash_truth(company_id: str, request: Request, as_of: Optional[dt.date] = None):
        return run_engine(request, company_id, "cash_truth", as_of=as_of or today())

    @app.get("/reports/{company_id}/pnl")
    def r_pnl(company_id: str, request: Request, month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
              basis: Literal["books", "corrected"] = "books"):
        return run_engine(request, company_id, "monthly_pnl", month=month, basis=basis)

    @app.get("/reports/{company_id}/kpis")
    def r_kpis(company_id: str, request: Request, as_of: Optional[dt.date] = None):
        return run_engine(request, company_id, "kpis", as_of=as_of or today())

    @app.get("/reports/{company_id}/forecast")
    def r_forecast(company_id: str, request: Request, horizon: int = Query(13, ge=1, le=24),
                   granularity: Literal["week", "month"] = "week", as_of: Optional[dt.date] = None):
        return run_engine(request, company_id, "cashflow_forecast", horizon=horizon, granularity=granularity,
                          as_of=as_of)

    @app.get("/reports/{company_id}/alerts")
    def r_alerts(company_id: str, request: Request, as_of: Optional[dt.date] = None):
        return run_engine(request, company_id, "alerts", as_of=as_of or today())

    @app.get("/reports/{company_id}/financing")
    def r_financing(company_id: str, request: Request, as_of: Optional[dt.date] = None):
        return run_engine(request, company_id, "financing_matches", as_of=as_of)

    @app.post("/reports/{company_id}/scenario")
    def r_scenario(company_id: str, body: ScenarioRequest, request: Request):
        from backend.engine.types import ScenarioChange

        try:
            changes = [ScenarioChange(**ch) for ch in body.changes]
        except Exception as e:
            raise HTTPException(422, f"invalid scenario change: {e}")
        return run_engine(request, company_id, "scenario", changes=changes, as_of=body.as_of)

    return app


app = create_app()
