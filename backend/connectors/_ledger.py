"""Ledger-type file readers: chart of accounts, parties, QuickBooks-style invoices/bills, journal (GL) exports,
cash-book notebooks (Google-Sheets style), POS daily summaries. Each reader takes a raw table (see _util.read_table)
plus a mapping profile and returns canonical frames. Rows that cannot be read are counted in warnings, never dropped silently.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

import pandas as pd

from backend.connectors._accounts import AccountCatalog, _RULES, infer_account
from backend.connectors._fx import FxResolver
from backend.connectors._stmt import _pick
from backend.connectors._util import clean, money_str, norm_header, parse_date, to_decimal
from backend.core.ids import make_id

ZERO = Decimal(0)


@dataclass
class LedgerCtx:
    company_id: str
    system: str
    file: str
    accounts: AccountCatalog
    fx: FxResolver
    base_currency: str
    sheet: Optional[str] = None
    parties: dict[tuple[str, str], str] = field(default_factory=dict)   # (kind, name) -> counterparty id
    warnings: list[str] = field(default_factory=list)

    def rowref(self, row: int) -> str:
        return f"{self.sheet}!R{row}" if self.sheet else str(row)


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ journal entries
def build_entries(ctx: LedgerCtx, entries: list[dict[str, Any]]) -> dict[str, pd.DataFrame]:
    """entries: [{date, reference, memo, row, lines: [{account_id, debit, credit, currency, rate, memo, counterparty_id}]}]"""
    je, jl = [], []
    unbalanced, plugged, plug_sum = [], 0, ZERO
    for e in entries:
        ref = ctx.rowref(e["row"])
        eid = make_id("je", ctx.company_id, ctx.system, ctx.file, ref)
        je.append({"id": eid, "company_id": ctx.company_id, "entry_date": e["date"], "reference": e.get("reference"),
                   "memo": e.get("memo"), "status": "posted", "origin": "imported", "source_system": ctx.system,
                   "source_file": ctx.file, "source_row_id": ref})
        bases, rates = [], []
        for ln in e["lines"]:
            rate = ln.get("rate")
            if rate is None:
                rate = ctx.fx.rate(ln["currency"], e["date"])
                if rate is None:
                    raise ValueError(f"{ctx.file}: no FX rate to {ctx.base_currency} for {ln['currency']} on {e['date']}")
            rates.append(rate)
            bases.append(((ln["debit"] or ZERO) - (ln["credit"] or ZERO)) * rate)
        total = sum(bases, ZERO)
        if total != 0:
            # A currency conversion booked at a rounded rate does not balance in base currency. When exactly one
            # line is in a foreign currency and the gap is small, the base-currency side is what the bank really
            # moved: keep it and let the foreign line absorb the difference (the stated fx_rate is kept as exported).
            foreign = [i for i, ln in enumerate(e["lines"]) if ln["currency"] != ctx.base_currency]
            gross = sum((abs(b) for b in bases), ZERO) / 2
            if len(foreign) == 1 and gross and abs(total) <= gross * Decimal("0.02"):
                i = foreign[0]
                plug_sum += abs(total)
                plugged += 1
                bases[i] -= total
                total = ZERO
        if abs(total) > Decimal("0.05"):
            unbalanced.append(ref)
        for k, ln in enumerate(e["lines"], 1):
            jl.append({"id": make_id("jl", eid, k), "entry_id": eid, "company_id": ctx.company_id, "line_no": k,
                       "account_id": ln["account_id"], "debit": money_str(ln["debit"] or ZERO),
                       "credit": money_str(ln["credit"] or ZERO), "currency": ln["currency"], "fx_rate": str(rates[k - 1]),
                       "amount_base": money_str(bases[k - 1]), "counterparty_id": ln.get("counterparty_id"),
                       "memo": ln.get("memo"), "source_system": ctx.system, "source_file": ctx.file,
                       "source_row_id": f"{ref}#{k}"})
    if plugged:
        ctx.warnings.append(f"{ctx.file}: {plugged} entries with one foreign-currency line did not balance in {ctx.base_currency} "
                            f"(rounded rate); the foreign line's base amount absorbed {money_str(plug_sum)} in total")
    if unbalanced:
        ctx.warnings.append(f"{ctx.file}: {len(unbalanced)} journal entries do not balance (first: {unbalanced[:5]}) - kept as exported")
    return {"journal_entries": _frame(je), "journal_lines": _frame(jl)}


def read_journal(df: pd.DataFrame, cols: dict[str, Any], opts: dict[str, Any], ctx: LedgerCtx) -> dict[str, pd.DataFrame]:
    """Voucher-style journal export (one row per line, rows of one voucher are consecutive)."""
    dayfirst = bool(opts.get("dayfirst", True))
    entries: list[dict[str, Any]] = []
    cur, bad, derived = None, [], {}
    for row in df.to_dict("records"):
        n = int(row["_row"])
        d = parse_date(_pick(row, df, cols.get("date")), dayfirst=dayfirst)
        dr = to_decimal(_pick(row, df, cols.get("debit")))
        cr = to_decimal(_pick(row, df, cols.get("credit")))
        code, name = clean(_pick(row, df, cols.get("account_code"))), clean(_pick(row, df, cols.get("account_name")))
        if d is None or (dr is None and cr is None) or not (code or name):
            bad.append(n)
            continue
        if dr is not None and dr < 0:                     # a negative debit is a credit (and vice versa)
            dr, cr = None, (cr or ZERO) - dr
        elif cr is not None and cr < 0:
            dr, cr = (dr or ZERO) - cr, None
        ccy = (clean(_pick(row, df, cols.get("currency"))) or ctx.base_currency).upper()
        rate = to_decimal(_pick(row, df, cols.get("rate")))
        if rate is None and ccy == ctx.base_currency:
            rate = Decimal(1)
        voucher = clean(_pick(row, df, cols.get("voucher")))
        memo = clean(_pick(row, df, cols.get("memo")))
        key = (voucher, d)
        if cur is None or cur["key"] != key:
            cur = {"key": key, "date": d, "reference": voucher, "memo": memo, "row": n, "lines": []}
            entries.append(cur)
        acct_id = ctx.accounts.account_id(code, name)
        if not ctx.accounts.by_code(code) and not ctx.accounts.by_name(name):
            at, st = infer_account(code, name or "")
            row_acct = {"id": acct_id, "code": code, "name": name or code, "type": at, "subtype": st, "currency": ccy}
            ctx.accounts.add(row_acct)
            derived[acct_id] = row_acct
        cp = clean(_pick(row, df, cols.get("counterparty")))
        cp_id = ctx.parties.get(("any", cp)) if cp else None
        cur["lines"].append({"account_id": acct_id, "debit": dr or ZERO, "credit": cr or ZERO, "currency": ccy,
                             "rate": rate, "memo": None, "counterparty_id": cp_id})
    out = build_entries(ctx, entries)
    if derived:
        rows = [{**a, "company_id": ctx.company_id, "source_system": ctx.system, "source_file": ctx.file,
                 "source_row_id": a["code"] or a["name"]} for a in derived.values()]
        out["accounts"] = _frame(rows)
        ctx.warnings.append(f"{ctx.file}: {len(rows)} accounts created from the journal's code/name columns; "
                            "account type/subtype were guessed from the name - owner to confirm")
    if bad:
        ctx.warnings.append(f"{ctx.file}: {len(bad)} journal rows skipped (no date/amount/account), first rows {bad[:5]}")
    return out


# ------------------------------------------------------------------ chart of accounts, parties
def read_coa(df, cols, opts, ctx: LedgerCtx) -> dict[str, pd.DataFrame]:
    rows = []
    for r in df.to_dict("records"):
        code, name = clean(_pick(r, df, cols.get("code"))), clean(_pick(r, df, cols.get("name")))
        if not name:
            continue
        atype = (clean(_pick(r, df, cols.get("type"))) or "").lower()
        sub = (clean(_pick(r, df, cols.get("subtype"))) or "").lower().replace(" ", "_")
        g_type, g_sub = infer_account(code, name)
        if atype not in ("asset", "liability", "equity", "income", "expense"):
            atype = {"revenue": "income", "cost of goods sold": "expense", "other income": "income"}.get(atype, g_type)
        known = {s for rules in _RULES.values() for _, s in rules} | {"other_asset", "other_liability", "opex", "revenue"}
        sub = sub if sub in known else (g_sub if atype == g_type else {"asset": "other_asset", "liability": "other_liability",
                                                                   "equity": "owner_equity", "income": "revenue",
                                                                   "expense": "opex"}[atype])
        acct_id = ctx.accounts.account_id(code, name)
        rec = {"id": acct_id, "company_id": ctx.company_id, "code": code, "name": name, "type": atype, "subtype": sub,
               "currency": ctx.base_currency, "is_active": True, "source_system": ctx.system, "source_file": ctx.file,
               "source_row_id": str(int(r["_row"]))}
        rows.append(rec)
        ctx.accounts.add({k: rec[k] for k in ("id", "code", "name", "type", "subtype", "currency")})
    return {"accounts": _frame(rows)}


def _terms_days(v: Any) -> Optional[int]:
    m = re.search(r"(\d+)", str(v or ""))
    return int(m.group(1)) if m else None


def party_id(ctx: LedgerCtx, kind: str, name: str) -> str:
    key = (kind, name)
    if key not in ctx.parties:
        ctx.parties[key] = f"{ctx.company_id}-{'cust' if kind == 'customer' else 'sup'}-{make_id('', name, length=8)[1:]}"
        ctx.parties[("any", name)] = ctx.parties[key]
    return ctx.parties[key]


def read_parties(df, cols, opts, ctx: LedgerCtx) -> dict[str, pd.DataFrame]:
    kind = opts.get("kind", "customer")
    rows = []
    for r in df.to_dict("records"):
        name = clean(_pick(r, df, cols.get("name")))
        if not name:
            continue
        rows.append({"id": party_id(ctx, kind, name), "company_id": ctx.company_id, "kind": kind, "name": name,
                     "payment_terms_days": _terms_days(_pick(r, df, cols.get("terms"))),
                     "source_system": ctx.system, "source_file": ctx.file, "source_row_id": str(int(r["_row"]))})
    return {"counterparties": _frame(rows)}


# ------------------------------------------------------------------ invoices & bills (QuickBooks-style CSV)
def _doc_lines(df, cols, ctx: LedgerCtx, number_key: str):
    """Group rows by document number -> {number: {"rows": [...], first_row}} keeping file order."""
    docs: dict[str, dict[str, Any]] = {}
    for r in df.to_dict("records"):
        no = clean(_pick(r, df, cols.get(number_key)))
        if not no:
            continue
        docs.setdefault(no, {"first": int(r["_row"]), "rows": []})["rows"].append(r)
    return docs


def _account_for(ctx: LedgerCtx, mapping: dict[str, str], key: Optional[str], subtype: str) -> Optional[dict[str, Any]]:
    if key and key in mapping:
        return ctx.accounts.by_code(mapping[key]) or ctx.accounts.by_name(mapping[key])
    if key:
        hit = ctx.accounts.by_name(key.split(":")[-1])
        if hit:
            return hit
    return ctx.accounts.by_subtype(subtype)


def _read_documents(df, cols, opts, ctx: LedgerCtx, *, kind: str) -> dict[str, pd.DataFrame]:
    inv = kind == "invoice"
    docs = _doc_lines(df, cols, ctx, "number")
    party_kind = "customer" if inv else "supplier"
    dayfirst = bool(opts.get("dayfirst", True))
    ar_ap = ctx.accounts.by_subtype("ar" if inv else "ap")
    tax_acct = ctx.accounts.by_subtype("tax_payable")
    if ar_ap is None:
        raise ValueError(f"{ctx.file}: no {'receivables' if inv else 'payables'} account in the chart of accounts - load the chart first")
    amap = opts.get("item_accounts" if inv else "category_accounts", {})
    out_docs, entries, parties = [], [], []
    for no, d in docs.items():
        first = d["rows"][0]
        issue = parse_date(_pick(first, df, cols.get("issue_date")), dayfirst=dayfirst)
        due = parse_date(_pick(first, df, cols.get("due_date")), dayfirst=dayfirst)
        who = clean(_pick(first, df, cols.get("party")))
        ccy = (clean(_pick(first, df, cols.get("currency"))) or ctx.base_currency).upper()
        if issue is None or not who:
            ctx.warnings.append(f"{ctx.file}: {kind} {no} skipped (no date or party)")
            continue
        rate = ctx.fx.rate(ccy, issue)
        if rate is None:
            raise ValueError(f"{ctx.file}: no FX rate for {ccy}")
        cp = party_id(ctx, party_kind, who)
        if not any(p["id"] == cp for p in parties):
            parties.append({"id": cp, "company_id": ctx.company_id, "kind": party_kind, "name": who,
                            "payment_terms_days": _terms_days(_pick(first, df, cols.get("terms"))),
                            "source_system": ctx.system, "source_file": ctx.file, "source_row_id": str(d["first"])})
        net, tax, lines_by_acct = ZERO, ZERO, {}
        for r in d["rows"]:
            amt = to_decimal(_pick(r, df, cols.get("amount"))) or ZERO
            tx = to_decimal(_pick(r, df, cols.get("tax"))) or ZERO
            net += amt
            tax += tx
            key = clean(_pick(r, df, cols.get("item" if inv else "category")))
            acct = _account_for(ctx, amap, key, "revenue" if inv else "inventory")
            if acct is None:
                raise ValueError(f"{ctx.file}: no account for {key!r}; set options.{'item' if inv else 'category'}_accounts")
            lines_by_acct[acct["id"]] = lines_by_acct.get(acct["id"], ZERO) + amt
        total = net + tax
        eid_row = d["first"]
        lines = []
        if inv:
            lines.append({"account_id": ar_ap["id"], "debit": total, "credit": ZERO, "currency": ccy, "rate": rate, "counterparty_id": cp})
            lines += [{"account_id": a, "debit": ZERO, "credit": v, "currency": ccy, "rate": rate} for a, v in lines_by_acct.items()]
            if tax and tax_acct:
                lines.append({"account_id": tax_acct["id"], "debit": ZERO, "credit": tax, "currency": ccy, "rate": rate})
        else:
            lines += [{"account_id": a, "debit": v, "credit": ZERO, "currency": ccy, "rate": rate} for a, v in lines_by_acct.items()]
            if tax and tax_acct:
                lines.append({"account_id": tax_acct["id"], "debit": tax, "credit": ZERO, "currency": ccy, "rate": rate})
            lines.append({"account_id": ar_ap["id"], "debit": ZERO, "credit": total, "currency": ccy, "rate": rate, "counterparty_id": cp})
        if tax and not tax_acct:
            ctx.warnings.append(f"{ctx.file}: tax on {no} has no tax-payable account; tax folded into the control line")
            lines = [ln for ln in lines]
        entry = {"date": issue, "reference": no, "memo": f"{'Invoice to' if inv else 'Bill from'} {who}", "row": eid_row, "lines": lines}
        entries.append(entry)
        je_id = make_id("je", ctx.company_id, ctx.system, ctx.file, ctx.rowref(eid_row))
        rec = {"id": make_id("inv" if inv else "bill", ctx.company_id, ctx.system, ctx.file, no),
               "company_id": ctx.company_id, "counterparty_id": cp, "issue_date": issue, "due_date": due, "currency": ccy,
               "fx_rate": str(rate), "total": money_str(total), "tax": money_str(tax), "total_base": money_str(total * rate),
               "status": "open", "journal_entry_id": je_id, "source_system": ctx.system, "source_file": ctx.file,
               "source_row_id": str(eid_row)}
        rec["invoice_no" if inv else "bill_no"] = no
        if not inv:
            rec["expense_account_id"] = next(iter(lines_by_acct))
        out_docs.append(rec)
    res = build_entries(ctx, entries)
    res["counterparties"] = _frame(parties)
    res["invoices" if inv else "bills"] = _frame(out_docs)
    return res


def read_invoices(df, cols, opts, ctx):
    return _read_documents(df, cols, opts, ctx, kind="invoice")


def read_bills(df, cols, opts, ctx):
    return _read_documents(df, cols, opts, ctx, kind="bill")


# ------------------------------------------------------------------ cash-book notebook
def _norm_label(s: Any) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", str(s or "").lower().replace("/", " ")).strip()


def _label_lookup(labels: dict[str, str]):
    table = {_norm_label(k): v for k, v in labels.items()}

    def find(raw: Any) -> Optional[str]:
        n = _norm_label(raw)
        if not n:
            return None
        if n in table:
            return table[n]
        m = difflib.get_close_matches(n, list(table), n=1, cutoff=0.78)   # head typos: 'Purchse', 'Delivary'
        return table[m[0]] if m else None
    return find


def read_cashbook(df, cols, opts, ctx: LedgerCtx) -> dict[str, pd.DataFrame]:
    """One row = one two-line entry. `Mode` names the cash/bank/wallet (or Udhar/Baki/Stock) side, `Head` the other
    side; In = Mode account debited, Out = credited. Opening-balance rows of a tab are merged into ONE entry
    (each row = one account line). Dates may be real dates or text in several formats; the tab name
    (e.g. 'Mar24') settles ambiguous day/month pairs."""
    amap = opts.get("account_map") or {}
    heads = _label_lookup({**amap.get("heads", {}), **amap.get("modes", {})})
    modes = _label_lookup({**amap.get("modes", {}), **amap.get("heads", {})})
    ref = _tab_ref(ctx.sheet)
    entries, opening, unmapped, bad = [], None, {}, []

    def acct(code: Optional[str], raw: Any):
        if code is None:
            unmapped[str(raw)] = unmapped.get(str(raw), 0) + 1
            return None
        a = ctx.accounts.by_code(code)
        return a["id"] if a else ctx.accounts.account_id(code, None)

    for r in df.to_dict("records"):
        n = int(r["_row"])
        d = parse_date(_pick(r, df, cols.get("date")), dayfirst=True, ref=ref)
        inn, out = to_decimal(_pick(r, df, cols.get("in"))), to_decimal(_pick(r, df, cols.get("out")))
        head_raw, mode_raw = _pick(r, df, cols.get("head")), _pick(r, df, cols.get("mode"))
        if d is None or (not inn and not out) or not clean(mode_raw):
            if clean(head_raw) or clean(mode_raw) or inn or out:
                bad.append(n)
            continue
        amount = inn if inn else out
        is_in = bool(inn)
        details = clean(_pick(r, df, cols.get("details")))
        note = clean(_pick(r, df, cols.get("note")))
        if _norm_label(head_raw) == "opening balance":
            mode_acct = acct(modes(mode_raw), mode_raw)
            if mode_acct is None:
                continue
            if opening is None:
                opening = {"date": d, "reference": "OPENING", "memo": details, "row": n, "lines": []}
                entries.append(opening)
            opening["lines"].append({"account_id": mode_acct, "debit": amount if is_in else ZERO,
                                     "credit": ZERO if is_in else amount, "currency": ctx.base_currency, "rate": Decimal(1)})
            continue
        a_mode, a_head = acct(modes(mode_raw), mode_raw), acct(heads(head_raw), head_raw)
        if a_mode is None or a_head is None:
            bad.append(n)
            continue
        entries.append({"date": d, "reference": note, "memo": details, "row": n, "lines": [
            {"account_id": a_mode if is_in else a_head, "debit": amount, "credit": ZERO,
             "currency": ctx.base_currency, "rate": Decimal(1)},
            {"account_id": a_head if is_in else a_mode, "debit": ZERO, "credit": amount,
             "currency": ctx.base_currency, "rate": Decimal(1)}]})
    if unmapped:
        ctx.warnings.append(f"{ctx.file}[{ctx.sheet}]: labels with no account in the profile's account_map: {unmapped} "
                            "- those rows were skipped; the owner should add them to the profile")
    if bad:
        ctx.warnings.append(f"{ctx.file}[{ctx.sheet}]: {len(bad)} rows skipped (no readable date/amount/labels), first rows {bad[:5]}")
    return build_entries(ctx, entries)


def _tab_ref(sheet: Optional[str]) -> Optional[tuple[int, int]]:
    m = re.fullmatch(r"([A-Za-z]{3})[ _-]?(\d{2,4})", sheet or "")
    if not m:
        return None
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    if m[1].lower() not in months:
        return None
    y = int(m[2])
    return (y + 2000 if y < 100 else y, months.index(m[1].lower()) + 1)


# ------------------------------------------------------------------ POS daily summary
def read_pos_daily(df, cols, opts, ctx: LedgerCtx) -> dict[str, pd.DataFrame]:
    rows, bad = [], []
    ccy = ctx.base_currency
    for r in df.to_dict("records"):
        n = int(r["_row"])
        d = parse_date(_pick(r, df, cols.get("date")), dayfirst=False)
        gross = to_decimal(_pick(r, df, cols.get("gross")))
        net = to_decimal(_pick(r, df, cols.get("net")))
        if d is None or gross is None or net is None:
            bad.append(n)
            continue

        def v(key):
            return money_str(to_decimal(_pick(r, df, cols.get(key))) or ZERO)
        card_amt = to_decimal(_pick(r, df, cols.get("card"))) or ZERO
        rows.append({"id": make_id("posdi", ctx.company_id, opts.get("location", ""), d.isoformat()),
                     "company_id": ctx.company_id, "sales_date": d, "location": opts.get("location"), "currency": ccy,
                     "gross_sales": money_str(gross), "discounts": v("discounts"), "refunds": "0.00",
                     "net_sales": money_str(net), "tax": v("tax"), "tips": "0.00", "service_charge": "0.00",
                     "fees": None, "cash_amount": v("cash"), "card_amount": money_str(card_amt),
                     "wallet_amount": "0.00", "aggregator_amount": "0.00", "other_amount": v("other"),
                     "transactions_count": int(to_decimal(_pick(r, df, cols.get("count"))) or 0),
                     "source_system": ctx.system, "source_file": ctx.file, "source_row_id": d.isoformat()})
    if bad:
        ctx.warnings.append(f"{ctx.file}: {len(bad)} daily rows skipped, first rows {bad[:5]}")
    return {"pos_daily_imports": _frame(rows)}


READERS = {"journal": read_journal, "coa": read_coa, "parties": read_parties, "invoices": read_invoices,
           "bills": read_bills, "cashbook": read_cashbook, "pos_daily": read_pos_daily}
