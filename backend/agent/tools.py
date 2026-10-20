"""Tool registry. Every tool declares a permission tier; the executor enforces it.

Tool parameters never include company_id: the executor binds the session's company, so
the model cannot reach another tenant's data by changing an argument.

Adding a tool (A1 / M1 / C1): write a handler `fn(ctx: ToolContext, args: dict) -> ToolResult`
and `registry.register(ToolSpec(...))`. Registration refuses anything that moves money.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from backend.agent.providers.base import ToolSchema
from backend.core.ids import is_valid_id, new_id
from backend.core.util import business_today, format_money, utc_now, utc_now_iso
from backend.ledger import queries
from backend.ledger.db import Database
from backend.ledger.lineage import get_source_rows, refs
from backend.security.tiers import MoneyMovementForbidden, Tier, looks_like_money_movement


class ToolResult(BaseModel):
    ok: bool = True
    summary: str = ""                        # code-written, numbers pre-formatted
    data: Any = None
    evidence: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    error: Optional[str] = None
    error_code: Optional[str] = None         # not_implemented | bad_args | engine_error | not_found | ...

    @classmethod
    def fail(cls, code: str, message: str) -> "ToolResult":
        return cls(ok=False, error=message, error_code=code)


@dataclass
class ToolContext:
    company_id: str
    db: Database
    actor: str = "agent"
    session_id: str | None = None
    approval_id: str | None = None
    settings: Any = None
    connector_registry: Any = None
    engine_factory: Callable[[Database], Any] | None = None

    def engine(self):
        if self.engine_factory is None:
            from backend.engine.interfaces import get_engine

            return get_engine(self.db)
        return self.engine_factory(self.db)


Handler = Callable[[ToolContext, dict], ToolResult]


@dataclass
class ToolSpec:
    name: str
    description: str
    tier: Tier
    parameters: dict
    handler: Handler
    precheck: Callable[[ToolContext, dict], Optional[str]] | None = None   # T2: validate before queueing
    title: Callable[[dict], str] | None = None                           # T2: approval inbox line
    moves_money: bool = False
    daily_cap: int | None = None                                         # T3 override of T3_DAILY_CAP

    def schema(self) -> ToolSchema:
        tier_note = {
            Tier.T0: "[T0 read]", Tier.T1: "[T1 analyse]",
            Tier.T2: "[T2 draft - queued for owner approval, nothing is written until approved]",
            Tier.T3: "[T3 limited action, capped per day]",
        }[self.tier]
        return ToolSchema(self.name, f"{self.description} {tier_note}", self.parameters)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.moves_money or looks_like_money_movement(spec.name, spec.description):
            raise MoneyMovementForbidden(f"tool {spec.name!r} looks like money movement; such tools may not exist")
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool {spec.name}")
        if "company_id" in spec.parameters.get("properties", {}):
            raise ValueError("tools must not take company_id; the executor binds it")
        spec.parameters.setdefault("additionalProperties", False)
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def schemas(self, max_tier: Tier | None = None) -> list[ToolSchema]:
        """Schemas shown to the model. Tools above the company's tier are hidden as well as blocked."""
        return [t.schema() for t in self._tools.values() if max_tier is None or max_tier.allows(t.tier)]


# ---------------------------------------------------------------- arg validation
_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def validate_args(schema: dict, args: Any, path: str = "args") -> list[str]:
    """Small JSON-Schema subset: type, required, properties, additionalProperties=false,
    enum, minimum/maximum, maxItems, items, format=date."""
    errs: list[str] = []
    t = schema.get("type")
    if t:
        py = _TYPES[t]
        if not isinstance(args, py) or (t in ("integer", "number") and isinstance(args, bool)):
            return [f"{path}: expected {t}"]
    if "enum" in schema and args not in schema["enum"]:
        errs.append(f"{path}: must be one of {schema['enum']}")
    if t in ("integer", "number"):
        if "minimum" in schema and args < schema["minimum"]:
            errs.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and args > schema["maximum"]:
            errs.append(f"{path}: must be <= {schema['maximum']}")
    if t == "string" and schema.get("format") == "date":
        try:
            dt.date.fromisoformat(args)
        except ValueError:
            errs.append(f"{path}: expected a date YYYY-MM-DD")
    if t == "string" and "pattern" in schema:
        import re

        if not re.fullmatch(schema["pattern"], args):
            errs.append(f"{path}: does not match {schema['pattern']}")
    if t == "array":
        if "maxItems" in schema and len(args) > schema["maxItems"]:
            errs.append(f"{path}: at most {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(args):
                errs += validate_args(schema["items"], item, f"{path}[{i}]")
    if t == "object":
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in args:
                errs.append(f"{path}.{req}: required")
        if schema.get("additionalProperties") is False:
            for k in args:
                if k not in props:
                    errs.append(f"{path}.{k}: unknown argument")
        for k, sub in props.items():
            if k in args:
                errs += validate_args(sub, args[k], f"{path}.{k}")
    return errs


# ---------------------------------------------------------------- helpers
def _date(args: dict, key: str, default: dt.date | None = None) -> dt.date:
    v = args.get(key)
    return dt.date.fromisoformat(v) if v else (default or business_today())


def _currency(ctx: ToolContext) -> str:
    c = queries.get_company(ctx.db, ctx.company_id)
    return c["base_currency"] if c else ""


def collect_evidence(obj: Any, limit: int = 200) -> tuple[list[str], int]:
    """All refs under any 'evidence' key (ordered, unique) and the total count."""
    seen: dict[str, None] = {}
    total = 0

    def walk(v: Any) -> None:
        nonlocal total
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "evidence" and isinstance(x, list):
                    for r in x:
                        if isinstance(r, str) and r not in seen:
                            seen[r] = None
                            total += 1
                else:
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(obj)
    return list(seen)[:limit], total


DATE = {"type": "string", "format": "date", "description": "YYYY-MM-DD; default today"}
STR_LIST = {"type": "array", "items": {"type": "string"}, "maxItems": 20}


# ---------------------------------------------------------------- T0 read tools
def _list_accounts(ctx: ToolContext, args: dict) -> ToolResult:
    rows = queries.list_accounts(ctx.db, ctx.company_id, args.get("subtypes"))
    return ToolResult(summary=f"{len(rows)} accounts in the chart of accounts.", data=rows,
                      evidence=refs("accounts", [r["id"] for r in rows]), evidence_count=len(rows))


def _account_balances(ctx: ToolContext, args: dict) -> ToolResult:
    as_of = _date(args, "as_of")
    cur = _currency(ctx)
    rows = queries.account_balances(ctx.db, ctx.company_id, as_of, args.get("subtypes"))
    total = sum((r["balance_base"] for r in rows), Decimal("0"))
    parts = "; ".join(f"{r['name']} {format_money(r['balance_base'], cur)}" for r in rows)
    ev = [ref for r in rows for ref in refs("journal_lines", r["evidence_line_ids"])]
    label = "/".join(args.get("subtypes") or ["all"])
    summary = (f"Book balance ({label} accounts, posted entries up to {as_of.isoformat()}): "
               f"{format_money(total, cur)} in total ({parts}).")
    data = [{**r, "balance_base": float(r["balance_base"]),
             "balance_text": format_money(r["balance_base"], cur)} for r in rows]
    return ToolResult(summary=summary, data={"as_of": as_of.isoformat(), "currency": cur, "accounts": data,
                                             "total_base": float(total), "total_text": format_money(total, cur)},
                      evidence=ev, evidence_count=sum(int(r["line_count"]) for r in rows))


def _search_transactions(ctx: ToolContext, args: dict) -> ToolResult:
    kw = dict(text=args.get("text"), date_from=_date(args, "date_from") if args.get("date_from") else None,
              date_to=_date(args, "date_to") if args.get("date_to") else None, limit=args.get("limit", 20))
    if args.get("source", "ledger") == "statements":
        rows = queries.search_statement_lines(ctx.db, ctx.company_id, **kw)
        table = "external_statement_lines"
    else:
        rows = queries.search_journal_lines(ctx.db, ctx.company_id, **kw)
        table = "journal_lines"
    from backend.ledger.lineage import _jsonable

    rows = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
    return ToolResult(summary=f"{len(rows)} matching {table.replace('_', ' ')} (newest first).", data=rows,
                      evidence=refs(table, [r["id"] for r in rows]), evidence_count=len(rows))


def _get_source_rows(ctx: ToolContext, args: dict) -> ToolResult:
    res = get_source_rows(ctx.db, args["refs"], company_id=ctx.company_id)
    return ToolResult(summary=f"{len(res['rows'])} source rows found, {len(res['missing'])} not found.",
                      data=res, evidence=[r["ref"] for r in res["rows"]], evidence_count=len(res["rows"]))


# ---------------------------------------------------------------- T1 engine tools
def _engine_tool(method: str, build_args: Callable[[ToolContext, dict], dict]) -> Handler:
    def handler(ctx: ToolContext, args: dict) -> ToolResult:
        from backend.engine.interfaces import EngineError, EngineNotImplemented

        try:
            result = getattr(ctx.engine(), method)(ctx.company_id, **build_args(ctx, args))
        except EngineNotImplemented as e:
            return ToolResult.fail("not_implemented", str(e))
        except EngineError as e:
            return ToolResult.fail("engine_error", str(e))
        data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        ev, total = collect_evidence(data)
        summary = (data.get("summary") if isinstance(data, dict) else "") or f"{method} computed by the engine."
        return ToolResult(summary=summary, data=data, evidence=ev, evidence_count=total)

    return handler


# ---------------------------------------------------------------- T2 draft tools
_LINE = {
    "type": "object",
    "properties": {
        "account_id": {"type": "string"},
        "debit": {"type": "number", "minimum": 0},
        "credit": {"type": "number", "minimum": 0},
        "counterparty_id": {"type": "string"},
        "memo": {"type": "string"},
    },
    "required": ["account_id"],
    "additionalProperties": False,
}


def _check_journal_draft(ctx: ToolContext, args: dict) -> Optional[str]:
    lines = args["lines"]
    if len(lines) < 2:
        return "a journal entry needs at least two lines"
    debit = sum(Decimal(str(l.get("debit", 0))) for l in lines)
    credit = sum(Decimal(str(l.get("credit", 0))) for l in lines)
    if debit != credit:
        return f"entry does not balance: debits {debit} != credits {credit}"
    if debit <= 0:
        return "entry has no amount"
    for l in lines:
        if bool(l.get("debit")) == bool(l.get("credit")):
            return "each line needs exactly one of debit or credit"
    known = {a["id"] for a in queries.list_accounts(ctx.db, ctx.company_id)}
    unknown = sorted({l["account_id"] for l in lines} - known)
    if unknown:
        return f"unknown account ids for this company: {unknown}"
    return _foreign_ids(ctx, "counterparties", [l.get("counterparty_id") for l in lines])


def _foreign_ids(ctx: ToolContext, table: str, ids: list) -> Optional[str]:
    """Tenant isolation for ids the model passes in: each must exist for THIS company."""
    wanted = sorted({i for i in ids if i})
    if not wanted:
        return None
    found = {r["id"] for r in ctx.db.query_dicts(
        f"SELECT id FROM {table} WHERE company_id = ? AND id IN (SELECT UNNEST(?))", [ctx.company_id, wanted])}
    missing = [i for i in wanted if i not in found]
    return f"unknown {table} ids for this company: {missing}" if missing else None


def _check_reminder(ctx: ToolContext, args: dict) -> Optional[str]:
    return (_foreign_ids(ctx, "counterparties", [args.get("counterparty_id")])
            or _foreign_ids(ctx, "invoices", [args.get("invoice_id")]))


def _write_journal_draft(ctx: ToolContext, args: dict) -> ToolResult:
    """Runs only after owner approval. Writes status='draft' rows - never posts."""
    import pandas as pd

    from backend.ledger.loader import load_dataframe

    err = _check_journal_draft(ctx, args)
    if err:
        return ToolResult.fail("bad_args", err)
    cur = _currency(ctx)
    entry_id = new_id("draft-je")
    src = {"source_system": "copilot", "source_file": f"approval:{ctx.approval_id or 'direct'}"}
    je = pd.DataFrame([{"id": entry_id, "company_id": ctx.company_id, "entry_date": args["entry_date"],
                        "memo": args["memo"], "reference": "COPILOT-DRAFT", "status": "draft",
                        "origin": "copilot_draft", "created_by": ctx.actor, "approval_id": ctx.approval_id,
                        "source_row_id": "entry", **src}])
    jl = pd.DataFrame([{
        "id": f"{entry_id}-{i}", "entry_id": entry_id, "company_id": ctx.company_id, "line_no": i,
        "account_id": l["account_id"], "debit": l.get("debit", 0), "credit": l.get("credit", 0),
        "currency": cur, "fx_rate": 1.0, "amount_base": l.get("debit", 0) - l.get("credit", 0),
        "counterparty_id": l.get("counterparty_id"), "memo": l.get("memo") or args["memo"],
        "source_row_id": f"line-{i}", **src} for i, l in enumerate(args["lines"], start=1)])
    batch = ctx.approval_id.replace("apr", "batch-apr") if ctx.approval_id else None
    load_dataframe(ctx.db, "journal_entries", je, batch_id=batch, scan_injection=False)
    load_dataframe(ctx.db, "journal_lines", jl, batch_id=f"{batch}-lines" if batch else None, scan_injection=False)
    total = sum(Decimal(str(l.get("debit", 0))) for l in args["lines"])
    return ToolResult(summary=f"Draft journal entry {entry_id} saved for {format_money(total, cur)} "
                              f"(status draft - not posted to the books).",
                      data={"entry_id": entry_id, "status": "draft"},
                      evidence=[f"journal_entries:{entry_id}"], evidence_count=1)


def _check_connector_draft(ctx: ToolContext, args: dict) -> Optional[str]:
    reg = ctx.connector_registry
    if reg is None:
        return "no connector registry available"
    try:
        cls = reg.get_class(args["connector_id"])
    except KeyError as e:
        return str(e)
    if not cls.metadata.supports_write_draft or cls.metadata.status == "planned":
        return f"connector {args['connector_id']} does not support draft writes"
    return None


def _connector_write_draft(ctx: ToolContext, args: dict) -> ToolResult:
    err = _check_connector_draft(ctx, args)
    if err:
        return ToolResult.fail("bad_args", err)
    conn = ctx.connector_registry.create(args["connector_id"], ctx.company_id, {"settings": ctx.settings})
    res = conn.write_draft(args["payload"])
    if res.ok and not res.sandbox:
        return ToolResult.fail("policy", "connector reported a non-sandbox write; refusing to report success")
    return ToolResult(ok=res.ok, summary=res.message or ("Draft created in sandbox." if res.ok else "Draft failed."),
                      data=res.model_dump(), error=None if res.ok else res.message,
                      error_code=None if res.ok else "connector_error")


# ---------------------------------------------------------------- T3 limited execute
def _mark_reminder_ready(ctx: ToolContext, args: dict) -> ToolResult:
    rid = new_id("rem")
    ctx.db.execute(
        "INSERT INTO reminders (id, company_id, counterparty_id, invoice_id, channel, body, status, created_by, "
        "created_at) VALUES (?,?,?,?,?,?, 'ready', ?, ?)",
        [rid, ctx.company_id, args.get("counterparty_id"), args.get("invoice_id"), args["channel"], args["body"],
         ctx.actor, utc_now_iso()],
    )
    return ToolResult(summary=f"Reminder {rid} marked ready for you to send via {args['channel']}. "
                              "The copilot does not send it.", data={"reminder_id": rid, "status": "ready"})


# ---------------------------------------------------------------- default registry
def default_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(ToolSpec("list_accounts", "List the company's chart of accounts.", Tier.T0,
                        {"type": "object", "properties": {"subtypes": STR_LIST}}, _list_accounts))
    r.register(ToolSpec("account_balances",
                        "Book balance per account from posted journal lines (e.g. subtypes cash, bank, wallet).",
                        Tier.T0, {"type": "object", "properties": {"as_of": DATE, "subtypes": STR_LIST}},
                        _account_balances))
    r.register(ToolSpec("search_transactions", "Search ledger lines or bank/wallet statement lines.", Tier.T0,
                        {"type": "object", "properties": {
                            "source": {"type": "string", "enum": ["ledger", "statements"]},
                            "text": {"type": "string"}, "date_from": DATE, "date_to": DATE,
                            "limit": {"type": "integer", "minimum": 1, "maximum": 50}}},
                        _search_transactions))
    r.register(ToolSpec("get_source_rows", "Fetch the original source rows (with lineage) behind evidence refs.",
                        Tier.T0, {"type": "object", "properties": {
                            "refs": {"type": "array", "items": {"type": "string"}, "maxItems": 50}},
                            "required": ["refs"]}, _get_source_rows))

    r.register(ToolSpec("cash_truth", "Compare book cash with real bank/wallet/cash balances; list causes of the "
                        "gap with evidence and proposed correcting drafts.", Tier.T1,
                        {"type": "object", "properties": {"as_of": DATE}},
                        _engine_tool("cash_truth", lambda c, a: {"as_of": _date(a, "as_of")})))
    r.register(ToolSpec("monthly_pnl", "One-page profit and loss for a month.", Tier.T1,
                        {"type": "object", "properties": {
                            "month": {"type": "string", "pattern": r"\d{4}-\d{2}", "description": "YYYY-MM"},
                            "basis": {"type": "string", "enum": ["books", "corrected"]}}, "required": ["month"]},
                        _engine_tool("monthly_pnl", lambda c, a: {"month": a["month"],
                                                                  "basis": a.get("basis", "books")})))
    r.register(ToolSpec("kpis", "Key business numbers: runway, margins, days to get paid, etc.", Tier.T1,
                        {"type": "object", "properties": {"as_of": DATE}},
                        _engine_tool("kpis", lambda c, a: {"as_of": _date(a, "as_of")})))
    r.register(ToolSpec("cashflow_forecast", "Cash forecast with ranges and backtest error.", Tier.T1,
                        {"type": "object", "properties": {
                            "horizon": {"type": "integer", "minimum": 1, "maximum": 24},
                            "granularity": {"type": "string", "enum": ["week", "month"]}}},
                        _engine_tool("cashflow_forecast", lambda c, a: {"horizon": a.get("horizon", 13),
                                                                        "granularity": a.get("granularity", "week")})))
    r.register(ToolSpec("alerts", "Current cash, customer and supplier alerts.", Tier.T1,
                        {"type": "object", "properties": {"as_of": DATE}},
                        _engine_tool("alerts", lambda c, a: {"as_of": _date(a, "as_of")})))
    r.register(ToolSpec("financing_matches", "Financing products that fit the business, with eligibility reasons "
                        "and affordability. Information only; never applies.", Tier.T1,
                        {"type": "object", "properties": {}},
                        _engine_tool("financing_matches", lambda c, a: {})))
    r.register(ToolSpec("run_scenario", "What-if forecast, e.g. raw material +15% or losing the biggest customer.",
                        Tier.T1, {"type": "object", "properties": {
                            "changes": {"type": "array", "maxItems": 10, "items": {"type": "object"}}},
                            "required": ["changes"]},
                        _engine_tool("scenario", lambda c, a: {"changes": _scenario_changes(a["changes"])})))

    r.register(ToolSpec("draft_journal_entry", "Propose a correcting journal entry. It is queued for the owner's "
                        "approval and, if approved, saved as a DRAFT (never posted).", Tier.T2,
                        {"type": "object", "properties": {
                            "entry_date": {"type": "string", "format": "date"},
                            "memo": {"type": "string"}, "reason": {"type": "string"},
                            "lines": {"type": "array", "items": _LINE, "maxItems": 20},
                            "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 50}},
                            "required": ["entry_date", "memo", "lines"]},
                        _write_journal_draft, precheck=_check_journal_draft,
                        title=lambda a: f"Draft journal entry: {a.get('memo', '')}"[:200]))
    r.register(ToolSpec("connector_write_draft", "Create a DRAFT object in a connected system's sandbox "
                        "(e.g. QuickBooks sandbox). Queued for owner approval.", Tier.T2,
                        {"type": "object", "properties": {"connector_id": {"type": "string"},
                                                          "payload": {"type": "object"}},
                         "required": ["connector_id", "payload"]},
                        _connector_write_draft, precheck=_check_connector_draft,
                        title=lambda a: f"Sandbox draft in {a.get('connector_id')}"))
    r.register(ToolSpec("mark_reminder_ready", "Save a payment-reminder text for a late customer and mark it ready "
                        "for the owner to send. The copilot never sends messages.", Tier.T3,
                        {"type": "object", "properties": {
                            "counterparty_id": {"type": "string"}, "invoice_id": {"type": "string"},
                            "channel": {"type": "string", "enum": ["email", "sms", "whatsapp", "letter"]},
                            "body": {"type": "string"}}, "required": ["channel", "body"]},
                        _mark_reminder_ready, precheck=_check_reminder))
    return r


def _scenario_changes(raw: list[dict]):
    from backend.engine.types import ScenarioChange

    return [ScenarioChange(**c) for c in raw]
