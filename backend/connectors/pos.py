"""Point of sale -> pos_orders / pos_order_lines / pos_payments (+ pos_daily_imports for daily-summary exports).

One adapter, several sources:
  * Square sandbox API (live): Locations, Orders search, Payments. Needs SQUARE_ACCESS_TOKEN (sandbox), optional
    SQUARE_LOCATION_ID. Only the sandbox is enabled; the production host is refused.
  * Export profiles (files): Square transactions + items CSV, Odoo POS order-lines export, Loyverse receipts export,
    Square daily sales summary. Each is a mapping profile in backend/connectors/profiles (see _mapping.py).

Conventions follow backend/ledger/SCHEMA.md: order amounts are positive magnitudes (a refund is its own order with
status 'refund'), payments are signed, business_date is the day in the location's time zone, times are stored in UTC,
`fee` is NULL unless the source states it. Pseudonymised/no customer data is ever read.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from backend.connectors._accounts import AccountCatalog
from backend.connectors._fx import FxResolver
from backend.connectors._ledger import LedgerCtx, read_pos_daily
from backend.connectors._mapping import ProfileStore
from backend.connectors._stmt import _pick
from backend.connectors._util import clean, money_str, parse_date, read_table, to_decimal
from backend.connectors.base import (ConnectionTestResult, Connector, ConnectorError, ConnectorMetadata, DatasetInfo,
                                     PullResult, env_value, register)
from backend.connectors._filebase import FILE_SUFFIXES
from backend.core.ids import make_id

ZERO = Decimal(0)
SQUARE_SANDBOX = "https://connect.squareupsandbox.com"
METHODS = {"CARD": "card", "CASH": "cash", "SQUARE_GIFT_CARD": "voucher", "WALLET": "wallet", "BANK_ACCOUNT": "bank_transfer",
           "THIRD_PARTY_CARD": "card", "BUY_NOW_PAY_LATER": "other", "OTHER": "other"}
FILE_TARGETS = ("pos_square_tx", "pos_square_items", "pos_odoo", "pos_loyverse", "pos_daily")


def _to_utc(local: dt.datetime, tz: Optional[str]) -> dt.datetime:
    if not tz:
        return local
    return local.replace(tzinfo=ZoneInfo(tz)).astimezone(dt.timezone.utc).replace(tzinfo=None)


def _method(text: Any) -> str:
    t = str(text or "").strip().lower()
    return {"card": "card", "cash": "cash", "bkash": "wallet", "nagad": "wallet", "wallet": "wallet", "voucher": "voucher",
            "gift card": "voucher", "bank": "bank_transfer", "bank transfer": "bank_transfer", "credit": "credit",
            "on account": "credit"}.get(t, "other")


class _Orders:
    """Collects orders (with lines and payments) from any source and builds the three canonical frames."""

    def __init__(self, company_id: str, system: str):
        self.cid, self.system = company_id, system
        self.orders: list[dict[str, Any]] = []
        self.lines: list[dict[str, Any]] = []
        self.pays: list[dict[str, Any]] = []
        self.seen: dict[str, int] = {}

    def add(self, *, ext_id: str, file: str, row: str, local_date: dt.date, opened: Optional[dt.datetime], tz: Optional[str],
            currency: str, status: str, gross: Decimal, discount: Decimal, tax: Decimal, tip: Decimal, service: Decimal,
            total: Decimal, location: Optional[str] = None, employee: Optional[str] = None, channel: str = "counter",
            refund_for: Optional[str] = None, lines: list[dict[str, Any]] | None = None,
            payments: list[dict[str, Any]] | None = None) -> str:
        oid = make_id("pord", self.cid, self.system, file, row)
        self.seen[ext_id] = self.seen.get(ext_id, 0) + 1
        self.orders.append({
            "id": oid, "company_id": self.cid, "external_order_id": ext_id, "location": location, "opened_at": opened,
            "closed_at": None, "business_date": local_date, "timezone": tz, "currency": currency, "channel": channel,
            "status": status, "refund_for_order_id": refund_for, "gross": money_str(gross), "discount": money_str(discount),
            "tax": money_str(tax), "tip": money_str(tip), "service_charge": money_str(service), "fee": None,
            "total": money_str(total), "employee_ref": employee, "source_system": self.system, "source_file": file,
            "source_row_id": row})
        for n, ln in enumerate(lines or [], 1):
            self.lines.append({
                "id": make_id("pline", oid, n), "company_id": self.cid, "order_id": oid, "line_no": n, "sku": ln.get("sku"),
                "name": ln["name"], "qty": str(ln["qty"]), "unit_price": str(ln["unit_price"]),
                "discount": money_str(ln.get("discount", ZERO)), "tax": money_str(ln.get("tax", ZERO)),
                "line_total": money_str(ln["line_total"]), "source_system": self.system,
                "source_file": ln.get("file", file), "source_row_id": str(ln.get("row", row))})
        for n, p in enumerate(payments or [], 1):
            self.pays.append({
                "id": make_id("ppay", oid, n), "company_id": self.cid, "order_id": oid, "method": p["method"],
                "provider": p.get("provider"), "amount": money_str(p["amount"]),
                "fee": money_str(p["fee"]) if p.get("fee") is not None else None, "currency": currency,
                "paid_at": p.get("paid_at", opened), "reference": p.get("reference"), "source_system": self.system,
                "source_file": file, "source_row_id": row})
        return oid

    def frames(self) -> tuple[dict[str, pd.DataFrame], list[str]]:
        warns = []
        dups = {k: v for k, v in self.seen.items() if v > 1}
        if dups:
            warns.append(f"{len(dups)} order ids appear more than once (kept, not dropped): {list(dups)[:5]}")
        out = {}
        for name, rows in (("pos_orders", self.orders), ("pos_order_lines", self.lines), ("pos_payments", self.pays)):
            if rows:
                out[name] = pd.DataFrame(rows)
        return out, warns


# ----------------------------------------------------------------------------------- file sources
def square_orders(tx: pd.DataFrame, tc: dict, topt: dict, items: pd.DataFrame | None, ic: dict, ctx: _Orders,
                  file_tx: str, file_items: str, currency: str, warns: list[str]) -> None:
    refund_events = {e.lower() for e in topt.get("refund_events", ["Refund"])}
    by_tx: dict[str, list[pd.Series]] = {}
    if items is not None:
        for r in items.to_dict("records"):
            by_tx.setdefault(clean(_pick(r, items, ic.get("order_id"))) or "", []).append(r)
    bad = []
    for r in tx.to_dict("records"):
        n = int(r["_row"])
        d = parse_date(_pick(r, tx, tc.get("date")), dayfirst=False)
        gross, tax = to_decimal(_pick(r, tx, tc.get("gross"))), to_decimal(_pick(r, tx, tc.get("tax")))
        total = to_decimal(_pick(r, tx, tc.get("total")))
        oid = clean(_pick(r, tx, tc.get("order_id")))
        if d is None or gross is None or total is None or not oid:
            bad.append(n)
            continue
        refund = (clean(_pick(r, tx, tc.get("event"))) or "").lower() in refund_events or total < 0
        sign = -1 if refund else 1
        tz = clean(_pick(r, tx, tc.get("timezone")))
        t = clean(_pick(r, tx, tc.get("time"))) or "00:00:00"
        opened = _to_utc(dt.datetime.combine(d, dt.time.fromisoformat(t if len(t) > 5 else t + ":00")), tz)
        cash, card = to_decimal(_pick(r, tx, tc.get("cash"))) or ZERO, to_decimal(_pick(r, tx, tc.get("card"))) or ZERO
        pays = [{"method": m, "amount": a, "reference": clean(_pick(r, tx, tc.get("payment_id")))}
                for m, a in (("cash", cash), ("card", card)) if a]
        lines = []
        for ir in by_tx.get(oid, []):
            q = to_decimal(_pick(ir, items, ic.get("qty"))) or ZERO
            g = to_decimal(_pick(ir, items, ic.get("gross"))) or ZERO
            disc = to_decimal(_pick(ir, items, ic.get("discount"))) or ZERO
            ltax = to_decimal(_pick(ir, items, ic.get("tax"))) or ZERO
            q, g, disc, ltax = abs(q), abs(g), abs(disc), abs(ltax)
            lines.append({"sku": clean(_pick(ir, items, ic.get("sku"))), "name": clean(_pick(ir, items, ic.get("name"))) or "item",
                          "qty": q, "unit_price": (g / q).quantize(Decimal("0.0001")) if q else g, "discount": disc,
                          "tax": ltax, "line_total": g - disc, "file": file_items, "row": int(ir["_row"])})
        ctx.add(ext_id=oid, file=file_tx, row=str(n), local_date=d, opened=opened, tz=tz, currency=currency,
                status="refund" if refund else "sale", gross=abs(gross), discount=abs(to_decimal(_pick(r, tx, tc.get("discount"))) or ZERO),
                tax=abs(tax or ZERO), tip=abs(to_decimal(_pick(r, tx, tc.get("tip"))) or ZERO), service=ZERO, total=abs(total),
                location=clean(_pick(r, tx, tc.get("location"))), employee=clean(_pick(r, tx, tc.get("staff"))),
                lines=lines, payments=pays)
    if bad:
        warns.append(f"{file_tx}: {len(bad)} rows skipped (no date/amount/id), first rows {bad[:5]}")
    if items is None:
        warns.append(f"{file_tx}: no items file supplied - orders have no lines")


def odoo_orders(df: pd.DataFrame, c: dict, opt: dict, ctx: _Orders, file: str, currency: str, warns: list[str]) -> None:
    groups: dict[str, list[pd.Series]] = {}
    for r in df.to_dict("records"):
        groups.setdefault(clean(_pick(r, df, c.get("order_id"))) or "", []).append(r)
    tz = opt.get("timezone")
    for oid, rows in groups.items():
        first = rows[0]
        raw = clean(_pick(first, df, c.get("datetime")))
        try:
            local_or_utc = dt.datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            warns.append(f"{file}: order {oid} skipped (unreadable date {raw!r})")
            continue
        utc = local_or_utc if opt.get("times_are_utc") else _to_utc(local_or_utc, tz)
        local = utc.replace(tzinfo=dt.timezone.utc).astimezone(ZoneInfo(tz)).date() if tz else utc.date()
        lines, tax, total, gross, refund = [], ZERO, ZERO, ZERO, False
        for r in rows:
            q = to_decimal(_pick(r, df, c.get("qty"))) or ZERO
            price = to_decimal(_pick(r, df, c.get("unit_price"))) or ZERO
            sub = to_decimal(_pick(r, df, c.get("subtotal"))) or ZERO
            tx = to_decimal(_pick(r, df, c.get("tax"))) or ZERO
            tot = to_decimal(_pick(r, df, c.get("total"))) or ZERO
            refund = refund or q < 0
            gross, tax, total = gross + abs(sub), tax + abs(tx), total + abs(tot)
            lines.append({"name": clean(_pick(r, df, c.get("name"))) or "item", "qty": abs(q), "unit_price": price,
                          "tax": abs(tx), "line_total": abs(sub), "row": int(r["_row"])})
        amount = total
        pays = [{"method": _method(_pick(first, df, c.get("method"))), "amount": -amount if refund else amount, "paid_at": utc}]
        ctx.add(ext_id=oid, file=file, row=str(int(first["_row"])), local_date=local, opened=utc, tz=tz, currency=currency,
                status="refund" if refund else "sale", gross=gross, discount=ZERO, tax=tax, tip=ZERO, service=ZERO, total=total,
                employee=clean(_pick(first, df, c.get("staff"))), lines=lines, payments=pays)


def loyverse_orders(df: pd.DataFrame, c: dict, opt: dict, ctx: _Orders, file: str, currency: str, warns: list[str]) -> None:
    tz = opt.get("timezone")
    for r in df.to_dict("records"):
        n = int(r["_row"])
        raw = clean(_pick(r, df, c.get("datetime")))
        try:
            when = dt.datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            warns.append(f"{file}: row {n} skipped (unreadable date)")
            continue
        utc = when if opt.get("times_are_utc") else _to_utc(when, tz)
        local = utc.replace(tzinfo=dt.timezone.utc).astimezone(ZoneInfo(tz)).date() if tz else utc.date()
        refund = (clean(_pick(r, df, c.get("type"))) or "").upper() == "REFUND"
        gross, tax = to_decimal(_pick(r, df, c.get("gross"))) or ZERO, to_decimal(_pick(r, df, c.get("tax"))) or ZERO
        total = to_decimal(_pick(r, df, c.get("total"))) or ZERO
        disc = to_decimal(_pick(r, df, c.get("discount"))) or ZERO
        ctx.add(ext_id=clean(_pick(r, df, c.get("order_id"))) or str(n), file=file, row=str(n), local_date=local, opened=utc, tz=tz,
                currency=currency, status="refund" if refund else "sale", gross=abs(gross), discount=abs(disc), tax=abs(tax),
                tip=ZERO, service=ZERO, total=abs(total),
                payments=[{"method": _method(_pick(r, df, c.get("method"))), "amount": -abs(total) if refund else abs(total), "paid_at": utc}])


# ----------------------------------------------------------------------------------- connector
@register
class PosConnector(Connector):
    metadata = ConnectorMetadata(
        id="pos", name="POS: Square sandbox API + Square / Odoo / Loyverse exports", category="pos", auth_type="api_key",
        status="sandbox",
        description="Order-level sales from a point of sale: a live pull from the Square SANDBOX API (orders + payments, with "
                    "processing fees) or the export files of Square, Odoo POS and Loyverse, mapped to the same orders / lines "
                    "/ payments tables (refunds, tips, tax, cash vs card, local business day). Daily-summary exports load as "
                    "daily imports. Production Square accounts are refused by design.",
        canonical_tables=["pos_orders", "pos_order_lines", "pos_payments", "pos_daily_imports"],
        env_vars=["SQUARE_ACCESS_TOKEN", "SQUARE_ENVIRONMENT", "SQUARE_LOCATION_ID"], modes=["file", "square-sandbox-api"])

    # ---- config
    def mode(self) -> str:
        m = self.config.get("mode")
        if m:
            return m
        return "file" if (self.config.get("paths") or self.config.get("path")) else "square_api"

    def paths(self) -> list[Path]:
        raw = self.config.get("paths") or self.config.get("path")
        items = [raw] if isinstance(raw, (str, Path)) else list(raw or [])
        out: list[Path] = []
        for it in items:
            p = Path(it)
            out += sorted(f for f in p.iterdir() if f.suffix.lower() in FILE_SUFFIXES) if p.is_dir() else [p]
        return out

    def _token(self) -> str:
        return self.config.get("access_token") or env_value("SQUARE_ACCESS_TOKEN")

    def _http(self) -> httpx.Client:
        return self.config.get("http_client") or httpx.Client(timeout=60)

    def _base(self) -> str:
        env = (self.config.get("environment") or env_value("SQUARE_ENVIRONMENT") or "sandbox").lower()
        if env != "sandbox":
            raise ConnectorError("SQUARE_ENVIRONMENT must be 'sandbox': this copilot never connects to a live Square account")
        return self.config.get("base_url") or SQUARE_SANDBOX

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = self._http().get(self._base() + path, params=params, headers={
            "Authorization": f"Bearer {self._token()}", "Square-Version": "2025-10-16", "Accept": "application/json"})
        if r.status_code != 200:
            raise ConnectorError(f"Square GET {path} failed (HTTP {r.status_code})")
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = self._http().post(self._base() + path, json=body, headers={
            "Authorization": f"Bearer {self._token()}", "Square-Version": "2025-10-16", "Accept": "application/json"})
        if r.status_code != 200:
            raise ConnectorError(f"Square POST {path} failed (HTTP {r.status_code})")
        return r.json()

    # ---- Connector API
    def test_connection(self) -> ConnectionTestResult:
        if self.mode() == "file":
            ps = self.paths()
            if not ps or not all(p.exists() for p in ps):
                return ConnectionTestResult(ok=False, message="not configured: give POS export file(s) ('path') or use the Square API")
            try:
                kinds = [self._classify(p)[0] for p in ps]
            except ConnectorError as e:
                return ConnectionTestResult(ok=False, message=str(e))
            return ConnectionTestResult(ok=True, message=f"{len(ps)} file(s) recognised", details={"layouts": kinds})
        if not self._token():
            return ConnectionTestResult(ok=False, message="not configured: set SQUARE_ACCESS_TOKEN (Square developer sandbox "
                                        "access token) in backend/.env; optional SQUARE_LOCATION_ID")
        try:
            locs = self._get("/v2/locations").get("locations", [])
        except ConnectorError as e:
            return ConnectionTestResult(ok=False, message=str(e))
        return ConnectionTestResult(ok=True, message=f"Square sandbox reachable, {len(locs)} location(s)",
                                    details={"locations": [l.get("name") for l in locs]})

    def discover(self) -> list[DatasetInfo]:
        if self.mode() == "file":
            return [DatasetInfo(id=p.name, name=p.name, canonical_table="pos_orders") for p in self.paths()]
        return [DatasetInfo(id="orders", name="Square orders (search)", canonical_table="pos_orders"),
                DatasetInfo(id="payments", name="Square payments (fees)", canonical_table="pos_payments")]

    def _classify(self, path: Path):
        df, _ = read_table(path)
        headers = [c for c in df.columns if not str(c).startswith("_")]
        prof = ProfileStore(self.config.get("profiles_dir")).match(headers)
        if prof is None or prof.target not in FILE_TARGETS:
            raise ConnectorError(f"{path.name}: not a recognised POS export layout (known: Square transactions/items/daily summary, "
                                 "Odoo order lines, Loyverse receipts); use csv_upload to map a new layout")
        return prof.target, prof, df

    def pull(self, since: dt.datetime | None = None) -> PullResult:
        return self._pull_file(since) if self.mode() == "file" else self._pull_square(since)

    # ---- file mode
    def _pull_file(self, since) -> PullResult:
        ps = self.paths()
        if not ps:
            raise ConnectorError("not configured: give POS export file(s) ('path')")
        currency = (self.config.get("currency") or self.config.get("base_currency") or "").upper()
        if not currency:
            raise ConnectorError("set config['currency'] (the POS currency, e.g. GBP)")
        warns: list[str] = []
        classified = [(p, *self._classify(p)) for p in ps]
        tx = next(((p, prof, df) for p, t, prof, df in classified if t == "pos_square_tx"), None)
        it = next(((p, prof, df) for p, t, prof, df in classified if t == "pos_square_items"), None)
        ctx = _Orders(self.company_id, "pos")
        frames: dict[str, list[pd.DataFrame]] = {}
        for p, target, prof, df in classified:
            ctx.system = prof.options.get("source_system", "pos")
            if target == "pos_square_tx":
                ctx.system = tx[1].options.get("source_system", "square_pos")
                square_orders(df, prof.columns, prof.options, it[2] if it else None, it[1].columns if it else {}, ctx,
                              p.name, it[0].name if it else "", currency, warns)
            elif target == "pos_odoo":
                odoo_orders(df, prof.columns, prof.options, ctx, p.name, currency, warns)
            elif target == "pos_loyverse":
                loyverse_orders(df, prof.columns, prof.options, ctx, p.name, currency, warns)
            elif target == "pos_daily":
                lc = LedgerCtx(self.company_id, prof.options.get("source_system", "pos"), p.name, AccountCatalog(self.company_id),
                               FxResolver(currency), currency)
                for t, f in read_pos_daily(df, prof.columns, prof.options, lc).items():
                    frames.setdefault(t, []).append(f)
                warns += lc.warnings
        # one source_system per frame is required by lineage only per row; rows carry their own
        out, w = ctx.frames()
        warns += w
        for t, f in out.items():
            if since is not None and t == "pos_orders":
                f = f[pd.to_datetime(f["business_date"]) >= pd.Timestamp(since.date())]
            frames.setdefault(t, []).append(f)
        if since is not None:
            keep = set(frames["pos_orders"][0]["id"]) if "pos_orders" in frames else set()
            for t, key in (("pos_order_lines", "order_id"), ("pos_payments", "order_id")):
                if t in frames:
                    frames[t] = [frames[t][0][frames[t][0][key].isin(keep)]]
        return PullResult(frames={t: pd.concat(v, ignore_index=True) for t, v in frames.items() if v},
                          source_files=[p.name for p in ps], warnings=warns)

    # ---- Square sandbox API
    def _pull_square(self, since) -> PullResult:
        if not self._token():
            raise ConnectorError("not configured: set SQUARE_ACCESS_TOKEN (Square developer sandbox access token)")
        locs = {l["id"]: l for l in self._get("/v2/locations").get("locations", [])}
        wanted = self.config.get("location_id") or env_value("SQUARE_LOCATION_ID")
        loc_ids = [wanted] if wanted else list(locs)
        if not loc_ids:
            raise ConnectorError("the Square sandbox account has no locations")
        start = (since or dt.datetime(2000, 1, 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # payments first: they carry the processing fees (orders do not)
        fees: dict[str, Decimal] = {}
        pay_ref: dict[str, dict[str, Any]] = {}
        for lid in loc_ids:
            cursor = None
            while True:
                q = {"location_id": lid, "begin_time": start, "limit": 100, **({"cursor": cursor} if cursor else {})}
                body = self._get("/v2/payments", q)
                for p in body.get("payments", []):
                    if "processing_fee" in p:                    # no fee block = fee unknown (stays NULL), not zero
                        fees[p["id"]] = sum((Decimal(f["amount_money"]["amount"]) for f in p["processing_fee"]), ZERO) / 100
                    pay_ref[p["id"]] = p
                cursor = body.get("cursor")
                if not cursor:
                    break
        ctx = _Orders(self.company_id, "square_pos")
        warns: list[str] = []
        n_orders = 0
        for lid in loc_ids:
            loc = locs.get(lid, {})
            tz, ccy = loc.get("timezone"), (loc.get("currency") or self.config.get("currency") or "USD").upper()
            cursor = None
            while True:
                body = self._post("/v2/orders/search", {
                    "location_ids": [lid], "limit": 200, **({"cursor": cursor} if cursor else {}),
                    "query": {"filter": {"state_filter": {"states": ["COMPLETED"]}, "date_time_filter": {"created_at": {"start_at": start}}},
                              "sort": {"sort_field": "CREATED_AT", "sort_order": "ASC"}}})
                for o in body.get("orders", []):
                    self._square_order(o, loc, tz, ccy, fees, pay_ref, ctx, warns)
                    n_orders += 1
                cursor = body.get("cursor")
                if not cursor:
                    break
        out, w = ctx.frames()
        return PullResult(frames=out, source_files=[f"square:{lid}" for lid in loc_ids], warnings=warns + w)

    @staticmethod
    def _m(money: Any) -> Decimal:
        return Decimal((money or {}).get("amount", 0)) / 100

    def _square_order(self, o, loc, tz, ccy, fees, pay_ref, ctx: _Orders, warns) -> None:
        created = dt.datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))
        utc = created.astimezone(dt.timezone.utc).replace(tzinfo=None)
        local = created.astimezone(ZoneInfo(tz)).date() if tz else utc.date()
        lines, gross = [], ZERO
        for li in o.get("line_items", []):
            q = Decimal(li.get("quantity", "1"))
            g = self._m(li.get("gross_sales_money") or li.get("base_price_money"))
            ltax = self._m(li.get("total_tax_money"))
            if any(t.get("type") == "INCLUSIVE" for t in li.get("taxes", [])):     # prices include tax -> make them ex-tax
                g -= ltax
            disc = self._m(li.get("total_discount_money"))
            gross += g
            lines.append({"sku": li.get("catalog_object_id"), "name": li.get("name", "item"), "qty": q,
                          "unit_price": (g / q).quantize(Decimal("0.0001")) if q else g, "discount": disc, "tax": ltax,
                          "line_total": g - disc})
        pays = []
        for t in o.get("tenders", []):
            pid = t.get("payment_id") or t.get("id")
            method = METHODS.get(t.get("type", "OTHER"), "other")
            brand = ((t.get("card_details") or {}).get("card") or {}).get("card_brand")
            pays.append({"method": method, "provider": brand.lower() if brand else None, "amount": self._m(t.get("amount_money")),
                         "fee": fees.get(pid), "paid_at": utc, "reference": pid})
        ctx.add(ext_id=o["id"], file=f"square:{o.get('location_id', '')}", row=o["id"], local_date=local, opened=utc, tz=tz,
                currency=ccy, status="sale", gross=gross, discount=self._m(o.get("total_discount_money")),
                tax=self._m(o.get("total_tax_money")), tip=self._m(o.get("total_tip_money")),
                service=self._m(o.get("total_service_charge_money")), total=self._m(o.get("total_money")),
                location=loc.get("name"), lines=lines, payments=pays)
        for rf in o.get("refunds", []):                   # a refund is its own order on the refund day
            rcreated = dt.datetime.fromisoformat(rf["created_at"].replace("Z", "+00:00"))
            rutc = rcreated.astimezone(dt.timezone.utc).replace(tzinfo=None)
            amt = self._m(rf.get("amount_money"))
            src = pay_ref.get(rf.get("tender_id"), {})
            method = METHODS.get(((o.get("tenders") or [{}])[0]).get("type", "OTHER"), "other")
            ctx.add(ext_id=rf["id"], file=f"square:{o.get('location_id', '')}", row=rf["id"],
                    local_date=rcreated.astimezone(ZoneInfo(tz)).date() if tz else rutc.date(), opened=rutc, tz=tz, currency=ccy,
                    status="refund", gross=amt, discount=ZERO, tax=ZERO, tip=ZERO, service=ZERO, total=amt, location=loc.get("name"),
                    refund_for=make_id("pord", self.company_id, "square_pos", f"square:{o.get('location_id', '')}", o["id"]),
                    payments=[{"method": method, "amount": -amt, "paid_at": rutc, "reference": rf.get("id")}])
            warns.append(f"refund {rf['id']}: Square gives only the amount, so tax/items of the refund are not split out") \
                if not any("refund" in w and "tax/items" in w for w in warns) else None
