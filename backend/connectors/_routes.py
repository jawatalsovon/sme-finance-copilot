"""HTTP routes for the connector flows. Mount from backend/api/main.py (orchestrator / A1) with:

    from backend.connectors._routes import build_router
    app.include_router(build_router(c, require_owner, actor_of))

`c(request)` returns the Container, `require_owner` is the owner-token dependency, `actor_of` the X-Actor dependency.

  POST /connectors/{id}/test                 body {config}                    -> ConnectionTestResult
  POST /connectors/{id}/discover             body {config}                    -> datasets
  POST /connectors/csv/propose               body {path, use_llm?, connector?} -> proposed column mapping (nothing loaded)
  POST /connectors/csv/confirm   [owner]     body {path, proposal, profile_id, name} -> saved reusable profile
  POST /connectors/{id}/ingest   [owner]     body {company_id, config, since?} -> pull + load into the ledger, audited
  GET  /connectors/qbo/authorize [owner]     -> {url}  (open it, approve in the QuickBooks SANDBOX)
  GET  /connectors/qbo/callback              Intuit redirects here; stores the tokens, shows a plain page

File paths from API callers must live under data/ or the private instance folder (no arbitrary server paths).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from backend.connectors._mapping import Proposal
from backend.connectors._qbo_client import QboClient, QboConfig, private_dir
from backend.connectors.base import ConnectorError

REPO = Path(__file__).resolve().parents[2]
_STATES: set[str] = set()


def _safe_path(raw: str) -> str:
    p = Path(raw)
    p = p if p.is_absolute() else REPO / p
    p = p.resolve()
    roots = [(REPO / "data").resolve(), private_dir().resolve()]
    if not any(p == r or r in p.parents for r in roots):
        raise HTTPException(400, "file paths must be inside the repo's data/ folder or the private instance folder")
    return str(p)


def _safe_config(cfg: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(cfg or {})
    for key in ("path", "paths", "fixtures_dir", "service_account_file", "token_file", "idempotency_file", "profiles_dir"):
        if key in cfg:
            cfg[key] = [_safe_path(x) for x in cfg[key]] if isinstance(cfg[key], list) else _safe_path(cfg[key])
    for forbidden in ("http_client", "access_token", "base_url", "qbo", "db", "provider"):   # never from the wire
        cfg.pop(forbidden, None)
    return cfg


class ConfigBody(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    company_id: str = "demo"


class ProposeBody(BaseModel):
    path: str
    use_llm: bool = False
    connector: str = "csv_upload"


class ConfirmBody(BaseModel):
    path: str
    proposal: dict[str, Any]
    profile_id: str
    name: str


class IngestBody(BaseModel):
    company_id: str
    config: dict[str, Any] = Field(default_factory=dict)
    since: Optional[dt.datetime] = None


def build_router(get_container, require_owner, actor_of) -> APIRouter:
    r = APIRouter(tags=["connectors"])

    def make(ct, connector_id: str, company_id: str, config: dict[str, Any]):
        try:
            return ct.connectors.create(connector_id, company_id, _safe_config(config))
        except KeyError as e:
            raise HTTPException(404, str(e))

    @r.post("/connectors/{connector_id}/test")
    def test(connector_id: str, body: ConfigBody, request: Request):
        return make(get_container(request), connector_id, body.company_id, body.config).test_connection().model_dump()

    @r.post("/connectors/{connector_id}/discover")
    def discover(connector_id: str, body: ConfigBody, request: Request):
        try:
            return [d.model_dump() for d in make(get_container(request), connector_id, body.company_id, body.config).discover()]
        except ConnectorError as e:
            raise HTTPException(400, str(e))

    @r.post("/connectors/csv/propose")
    def propose(body: ProposeBody, request: Request):
        ct = get_container(request)
        conn = make(ct, body.connector, "proposal", {})
        try:
            prop = conn.propose(_safe_path(body.path), use_llm=body.use_llm, provider=ct.provider if body.use_llm else None)
        except ConnectorError as e:
            raise HTTPException(400, str(e))
        return prop.model_dump()

    @r.post("/connectors/csv/confirm", dependencies=[Depends(require_owner)])
    def confirm(body: ConfirmBody, request: Request, actor: str = Depends(actor_of)):
        ct = get_container(request)
        conn = make(ct, "csv_upload", "proposal", {})
        prof = conn.confirm(Proposal.model_validate({**body.proposal, "confirmed": True}), body.profile_id, body.name,
                            _safe_path(body.path))
        ct.audit.append("mapping_profile_saved", actor=actor, payload={"profile_id": prof.id, "target": prof.target})
        return prof.model_dump()

    @r.post("/connectors/{connector_id}/ingest", dependencies=[Depends(require_owner)])
    def ingest(connector_id: str, body: IngestBody, request: Request, actor: str = Depends(actor_of)):
        from backend.ledger.loader import load_frames
        ct = get_container(request)
        cfg = _safe_config(body.config)
        cfg.setdefault("db", ct.db)                      # lets connectors read fx_rates / the chart of accounts
        conn = ct.connectors.create(connector_id, body.company_id, cfg)
        try:
            res = conn.pull(body.since)
        except ConnectorError as e:
            raise HTTPException(400, str(e))
        reports = load_frames(ct.db, res.frames, company_id=body.company_id, connector_id=connector_id, if_exists="skip")
        summary = {t: {"received": rep.received, "inserted": rep.inserted, "rejected": rep.rejected_count,
                       "skipped_existing": rep.skipped_existing, "injection_flags": len(rep.injection_flags)}
                   for t, rep in reports.items()}
        ct.audit.append("connector_ingest", actor=actor, company_id=body.company_id,
                        payload={"connector": connector_id, "files": res.source_files, "tables": summary})
        return {"tables": summary, "warnings": res.warnings, "mapping": res.column_mapping}

    @r.get("/connectors/qbo/authorize", dependencies=[Depends(require_owner)])
    def qbo_authorize():
        cfg = QboConfig()
        if not (cfg.client_id and cfg.client_secret):
            raise HTTPException(400, "not configured: set QBO_CLIENT_ID and QBO_CLIENT_SECRET in backend/.env")
        url, state = QboClient.authorize_url(cfg)
        _STATES.add(state)
        return {"url": url, "redirect_uri": cfg.redirect_uri}

    @r.get("/connectors/qbo/callback", response_class=PlainTextResponse)
    def qbo_callback(request: Request, code: str = Query(...), state: str = Query(...), realmId: str = Query("")):
        if state not in _STATES:
            raise HTTPException(400, "unknown or expired authorisation state; start again")
        _STATES.discard(state)
        cfg = QboConfig()
        try:
            QboClient(cfg).exchange_code(code, realmId or None)
        except ConnectorError as e:
            raise HTTPException(400, str(e))
        get_container(request).audit.append("qbo_authorised", actor="owner", payload={"realm_id": realmId or cfg.realm_id})
        return f"QuickBooks sandbox connected (company {realmId or cfg.realm_id}). Put QBO_REALM_ID={realmId or cfg.realm_id} in backend/.env if it is not there yet. You can close this tab."

    return r
