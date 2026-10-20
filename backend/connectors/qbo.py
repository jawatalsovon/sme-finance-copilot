"""QuickBooks Online (SANDBOX) connector: accounts, customers, vendors, invoices, bills, payments, journal entries
-> canonical ledger tables, and `write_draft` for journal entries only (owner-approved, sandbox only).

Live mode needs QBO_CLIENT_ID, QBO_CLIENT_SECRET, QBO_REALM_ID and QBO_REFRESH_TOKEN (see _qbo_client.py); without
them `test_connection()` reports "not configured" and `pull()` refuses. Tests and offline demos replay recorded-style
responses: config {"fixtures_dir": "..."} or an injected {"http_client": httpx.Client(transport=...)}.

What is posted to the journal: QuickBooks keeps invoices/bills/payments/receipts as documents whose postings are implicit,
so each is turned into a balanced journal entry from the accounts QuickBooks itself assigns (item income/expense
accounts, A/R, A/P, deposit and bank accounts, tax payable). Not yet mapped (listed in warnings): deposits, transfers,
credit memos, vendor credits, payroll.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import httpx
import pandas as pd

from backend.connectors._accounts import AccountCatalog
from backend.connectors._ledger import LedgerCtx, build_entries
from backend.connectors._qbo_client import QboClient, QboConfig, private_dir
from backend.connectors._util import clean, money_str
from backend.connectors.base import (ConnectionTestResult, Connector, ConnectorError, ConnectorMetadata, DatasetInfo,
                                     DraftWriteResult, PullResult, register)
from backend.core.ids import make_id

ZERO = Decimal(0)
SYSTEM = "quickbooks"

_TYPE: dict[str, tuple[str, str]] = {
    "Bank": ("asset", "bank"), "Accounts Receivable": ("asset", "ar"), "Other Current Asset": ("asset", "other_asset"),
    "Fixed Asset": ("asset", "fixed_asset"), "Other Asset": ("asset", "other_asset"),
    "Accounts Payable": ("liability", "ap"), "Credit Card": ("liability", "other_liability"),
    "Other Current Liability": ("liability", "other_liability"), "Long Term Liability": ("liability", "loan"),
    "Equity": ("equity", "owner_equity"), "Income": ("income", "revenue"), "Other Income": ("income", "other_income"),
    "Cost of Goods Sold": ("expense", "cogs"), "Expense": ("expense", "opex"), "Other Expense": ("expense", "other_expense"),
}
_SUB: dict[str, str] = {
    "CashOnHand": "cash", "Checking": "bank", "Savings": "bank", "MoneyMarket": "bank", "Inventory": "inventory",
    "PrepaidExpenses": "prepaid", "SalesTaxPayable": "tax_payable", "PayrollTaxPayable": "payroll_payable",
    "PayrollClearing": "payroll_payable", "RetainedEarnings": "retained_earnings", "OwnersEquity": "owner_equity",
    "PartnersEquity": "owner_equity", "OtherPrimaryIncome": "other_income", "InterestEarned": "other_income",
    "DividendIncome": "other_income", "PayrollExpenses": "payroll", "RentOrLeaseOfBuildings": "rent",
    "Utilities": "utilities", "AdvertisingPromotional": "marketing", "InterestPaid": "interest",
    "TaxesPaid": "tax", "ExchangeGainOrLoss": "fx_loss", "SuppliesMaterialsCogs": "cogs",
}


def map_account(a: dict[str, Any], company_id: str, batch_row: int) -> dict[str, Any]:
    atype, sub = _TYPE.get(a.get("AccountType", ""), ("expense", "opex"))
    sub = _SUB.get(a.get("AccountSubType", ""), sub)
    qid = str(a["Id"])
    return {"id": f"{company_id}-qbo{qid}", "company_id": company_id, "code": clean(a.get("AcctNum")) or qid,
            "name": a.get("FullyQualifiedName") or a.get("Name"), "type": atype, "subtype": sub,
            "currency": (a.get("CurrencyRef") or {}).get("value", "USD"), "is_active": bool(a.get("Active", True)),
            "external_ref": qid, "source_system": SYSTEM, "source_file": "qbo:Account", "source_row_id": qid}


def _d(x: Any) -> Decimal:
    return Decimal(str(x)) if x not in (None, "") else ZERO


def _date(s: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


@register
class QuickBooksConnector(Connector):
    metadata = ConnectorMetadata(
        id="qbo", name="QuickBooks Online (sandbox)", category="accounting", auth_type="oauth2", status="sandbox",
        description="Pulls the chart of accounts, customers, vendors, invoices, bills, payments and journal entries from a "
                    "QuickBooks Online SANDBOX company (OAuth2) into the ledger, and can write a DRAFT journal entry back "
                    "to the sandbox after the owner approves it. Production QuickBooks companies are refused by design.",
        canonical_tables=["accounts", "counterparties", "invoices", "invoice_payments", "bills", "bill_payments",
                          "journal_entries", "journal_lines"],
        supports_write_draft=True, docs_url="https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/account",
        env_vars=["QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REALM_ID", "QBO_REFRESH_TOKEN", "QBO_ENV", "QBO_REDIRECT_URI"],
        modes=["sandbox-api", "fixture-replay"])

    # ------------------------------------------------------------------ plumbing
    def _cfg(self) -> QboConfig:
        return QboConfig(self.config.get("qbo"))

    def _http(self) -> httpx.Client:
        if self.config.get("http_client") is not None:
            return self.config["http_client"]
        if self.config.get("fixtures_dir"):
            from backend.connectors._qbo_fixture import FixtureTransport
            self.config["http_client"] = httpx.Client(transport=FixtureTransport(self.config["fixtures_dir"]))
            return self.config["http_client"]
        return httpx.Client(timeout=60)

    def _replay(self) -> bool:
        return bool(self.config.get("fixtures_dir") or self.config.get("http_client") is not None)

    def client(self) -> QboClient:
        cfg = self._cfg()
        if self._replay():                           # recorded responses: placeholder credentials are fine
            cfg.client_id, cfg.client_secret = cfg.client_id or "fixture", cfg.client_secret or "fixture"
            cfg.realm_id = cfg.realm_id or "9341450000000000"
            cfg.refresh_token_env = cfg.refresh_token_env or "rt-fixture"
            cfg.token_file = Path(self.config.get("token_file") or private_dir() / "qbo_tokens.fixture.json")
        missing = cfg.missing()
        if missing:
            raise ConnectorError("not configured: missing " + ", ".join(missing))
        return QboClient(cfg, self._http())

    def test_connection(self) -> ConnectionTestResult:
        cfg = self._cfg()
        if not self._replay() and cfg.missing():
            return ConnectionTestResult(ok=False, message="not configured: set " + ", ".join(cfg.missing()) +
                                        " in backend/.env (one-time authorisation: python -m backend.connectors.qbo authorize)")
        try:
            c = self.client()
            info = c.get(f"companyinfo/{c.cfg.realm_id}")["CompanyInfo"]
        except ConnectorError as e:
            return ConnectionTestResult(ok=False, message=str(e))
        return ConnectionTestResult(ok=True, message=f"connected to sandbox company {info.get('CompanyName', '?')}",
                                    details={"replay": self._replay(), "country": info.get("Country")})

    def discover(self) -> list[DatasetInfo]:
        return [DatasetInfo(id=i, name=n, canonical_table=t) for i, n, t in [
            ("Account", "Chart of accounts", "accounts"), ("Customer", "Customers", "counterparties"),
            ("Vendor", "Vendors", "counterparties"), ("Invoice", "Invoices", "invoices"), ("Bill", "Bills", "bills"),
            ("Payment", "Customer payments", "invoice_payments"), ("BillPayment", "Supplier payments", "bill_payments"),
            ("JournalEntry", "Journal entries", "journal_entries")]]

    # ------------------------------------------------------------------ pull
    def pull(self, since: dt.datetime | None = None) -> PullResult:
        c = self.client()
        where = f"MetaData.LastUpdatedTime > '{since.strftime('%Y-%m-%dT%H:%M:%S')}-00:00'" if since else ""
        raw = {e: c.query_all(e, where if e != "Account" else "") for e in
               ["Account", "Customer", "Vendor", "Item", "Invoice", "Bill", "Payment", "BillPayment", "JournalEntry",
                "Purchase", "SalesReceipt"]}
        base = (self.config.get("base_currency") or "").upper()
        if not base:
            prefs = c.get("preferences")
            base = prefs["Preferences"]["CurrencyPrefs"]["HomeCurrency"]["value"].upper()
        cid = self.company_id
        accounts = [map_account(a, cid, i) for i, a in enumerate(raw["Account"])]
        catalog = AccountCatalog(cid, pd.DataFrame(accounts) if accounts else None)
        ctx = LedgerCtx(cid, SYSTEM, "qbo", catalog, _FlatFx(base), base)
        parties, party_rows = {}, []
        for kind, ent, key in (("customer", "Customer", "customers"), ("supplier", "Vendor", "vendors")):
            for p in raw[ent]:
                pid = f"{cid}-qbo{'c' if kind == 'customer' else 'v'}{p['Id']}"
                parties[(ent, str(p["Id"]))] = pid
                party_rows.append({"id": pid, "company_id": cid, "kind": kind, "name": p.get("DisplayName") or p.get("CompanyName"),
                                   "email": (p.get("PrimaryEmailAddr") or {}).get("Address"),
                                   "phone": (p.get("PrimaryPhone") or {}).get("FreeFormNumber"), "country": None,
                                   "external_ref": str(p["Id"]), "source_system": SYSTEM,
                                   "source_file": f"qbo:{ent}", "source_row_id": str(p["Id"])})
        items = {str(i["Id"]): i for i in raw["Item"]}
        warns: list[str] = []
        t = _Txns(ctx, catalog, parties, items, base, warns)
        for inv in raw["Invoice"]:
            t.invoice(inv)
        for b in raw["Bill"]:
            t.bill(b)
        for p in raw["Payment"]:
            t.payment(p)
        for bp in raw["BillPayment"]:
            t.bill_payment(bp)
        for pu in raw["Purchase"]:
            t.purchase(pu)
        for sr in raw["SalesReceipt"]:
            t.sales_receipt(sr)
        for je in raw["JournalEntry"]:
            t.journal(je)
        frames: dict[str, pd.DataFrame] = {"accounts": pd.DataFrame(accounts),
                                           "counterparties": pd.DataFrame(party_rows)}
        # one build per source_file so each QBO entity keeps its own lineage name
        je_parts, jl_parts = [], []
        for src, entries in t.entries.items():
            ctx.file = src
            built = build_entries(ctx, entries)
            je_parts.append(built["journal_entries"])
            jl_parts.append(built["journal_lines"])
        warns += ctx.warnings
        if je_parts:
            frames["journal_entries"] = pd.concat(je_parts, ignore_index=True)
            frames["journal_lines"] = pd.concat(jl_parts, ignore_index=True)
        for name, rows in (("invoices", t.invoices), ("bills", t.bills), ("invoice_payments", t.inv_pay),
                           ("bill_payments", t.bill_pay)):
            if rows:
                frames[name] = pd.DataFrame(rows)
        warns.append("not mapped yet: Deposit, Transfer, CreditMemo, VendorCredit, payroll (listed so nothing disappears silently)")
        return PullResult(frames={k: v for k, v in frames.items() if len(v)}, source_files=[f"qbo:{e}" for e, r in raw.items() if r],
                          warnings=warns)

    # ------------------------------------------------------------------ write_draft
    def _idem_file(self) -> Path:
        return Path(self.config.get("idempotency_file") or private_dir() / "qbo_draft_keys.json")

    @staticmethod
    def draft_key(company_id: str, payload: dict[str, Any]) -> str:
        """Same approval (or same content) -> same key -> at most one journal entry in the sandbox."""
        basis = payload.get("approval_id") or json.dumps({k: payload.get(k) for k in ("date", "memo", "reference", "lines")},
                                                          sort_keys=True, default=str)
        return hashlib.sha256(f"{company_id}|{basis}".encode()).hexdigest()[:40]

    def write_draft(self, payload: dict[str, Any]) -> DraftWriteResult:
        """payload: {date, memo, reference?, approval_id?, lines: [{account, debit?, credit?, description?}]}.
        `account` is a QuickBooks account id, a canonical account id from pull() ('<company>-qbo<Id>'), or an exact name."""
        c = self.client()                                              # refuses non-sandbox and missing credentials
        lines = payload.get("lines") or []
        if len(lines) < 2:
            return DraftWriteResult(ok=False, sandbox=True, message="a journal entry needs at least two lines")
        total_d = sum((_d(l.get("debit")) for l in lines), ZERO)
        total_c = sum((_d(l.get("credit")) for l in lines), ZERO)
        if total_d != total_c or total_d <= 0 or any(_d(l.get("debit")) < 0 or _d(l.get("credit")) < 0 for l in lines):
            return DraftWriteResult(ok=False, sandbox=True, message=f"entry does not balance (debits {total_d}, credits {total_c})")
        key = self.draft_key(self.company_id, payload)
        idem = self._idem_file()
        known = json.loads(idem.read_text(encoding="utf-8")) if idem.exists() else {}
        if key in known:
            return DraftWriteResult(ok=True, sandbox=True, external_id=known[key],
                                    message=f"already created in the sandbox as journal entry {known[key]} (idempotent)")
        names = None
        qlines = []
        for l in lines:
            ref = str(l.get("account", ""))
            if ref.startswith(f"{self.company_id}-qbo"):
                ref = ref[len(f"{self.company_id}-qbo"):]
            if not ref.isdigit():
                if names is None:
                    names = {a["Name"]: str(a["Id"]) for a in c.query_all("Account")} | \
                            {a.get("FullyQualifiedName", ""): str(a["Id"]) for a in c.query_all("Account")}
                if ref not in names:
                    return DraftWriteResult(ok=False, sandbox=True, message=f"unknown account {l.get('account')!r}")
                ref = names[ref]
            posting, amount = ("Debit", _d(l.get("debit"))) if _d(l.get("debit")) > 0 else ("Credit", _d(l.get("credit")))
            qlines.append({"DetailType": "JournalEntryLineDetail", "Amount": float(amount),
                           "Description": str(l.get("description") or payload.get("memo") or "")[:4000],
                           "JournalEntryLineDetail": {"PostingType": posting, "AccountRef": {"value": ref}}})
        body = {"TxnDate": str(payload.get("date") or dt.date.today()), "DocNumber": ("CPL-" + key[:8]),
                "PrivateNote": f"[DRAFT by SME Finance Copilot - approval {payload.get('approval_id', 'n/a')}] {payload.get('memo', '')}"[:4000],
                "Line": qlines}
        res = c.post("journalentry", body, request_id=key)
        ext = str(res["JournalEntry"]["Id"])
        known[key] = ext
        idem.parent.mkdir(parents=True, exist_ok=True)
        idem.write_text(json.dumps(known), encoding="utf-8")
        return DraftWriteResult(ok=True, sandbox=True, external_id=ext,
                                message=f"journal entry {ext} created in the QuickBooks sandbox (DocNumber CPL-{key[:8]})")


class _FlatFx:
    """QuickBooks gives the exchange rate on each transaction; this resolver only serves the base currency."""

    def __init__(self, base: str):
        self.base = base

    def rate(self, ccy: str, on):
        return Decimal(1) if ccy.upper() == self.base else None


class _Txns:
    """Turns QuickBooks transactions into journal entries (grouped per QBO entity) and sub-ledger documents."""

    def __init__(self, ctx, catalog, parties, items, base, warns):
        self.ctx, self.cat, self.parties, self.items, self.base, self.warns = ctx, catalog, parties, items, base, warns
        self.entries: dict[str, list[dict[str, Any]]] = {}
        self.invoices, self.bills, self.inv_pay, self.bill_pay = [], [], [], []
        self.cid = ctx.company_id

    # -- helpers
    def aid(self, ref: Any) -> Optional[str]:
        v = (ref or {}).get("value") if isinstance(ref, dict) else ref
        return f"{self.cid}-qbo{v}" if v is not None else None

    def special(self, subtype: str) -> Optional[str]:
        a = self.cat.by_subtype(subtype)
        return a["id"] if a else None

    def money(self, t: dict[str, Any]) -> tuple[str, Decimal]:
        ccy = (t.get("CurrencyRef") or {}).get("value", self.base).upper()
        return ccy, (_d(t.get("ExchangeRate")) if t.get("ExchangeRate") else Decimal(1)) if ccy != self.base else Decimal(1)

    def add(self, entity: str, t: dict[str, Any], memo: str, lines: list[dict[str, Any]]) -> Optional[str]:
        d = _date(t.get("TxnDate"))
        if d is None or not lines:
            self.warns.append(f"{entity} {t.get('Id')} skipped (no date or no postable lines)")
            return None
        if any(ln["account_id"] is None for ln in lines):
            self.warns.append(f"{entity} {t.get('Id')} skipped: a line points to an account that is not in the chart of accounts")
            return None
        ccy, rate = self.money(t)
        for ln in lines:
            ln.setdefault("currency", ccy)
            ln.setdefault("rate", rate)
        row = str(t["Id"])
        self.entries.setdefault(f"qbo:{entity}", []).append(
            {"date": d, "reference": clean(t.get("DocNumber")), "memo": memo, "row": row, "lines": lines})
        return make_id("je", self.cid, SYSTEM, f"qbo:{entity}", row)

    def line(self, account: Optional[str], debit: Decimal = ZERO, credit: Decimal = ZERO, cp: Optional[str] = None):
        return {"account_id": account, "debit": debit, "credit": credit, "counterparty_id": cp}

    def item_account(self, ref: Any, expense: bool) -> Optional[str]:
        it = self.items.get(str((ref or {}).get("value")))
        if not it:
            return None
        if expense:       # stock items are bought into the inventory asset account, other items to their expense account
            return self.aid(it.get("AssetAccountRef") if it.get("Type") == "Inventory" and it.get("AssetAccountRef")
                            else it.get("ExpenseAccountRef"))
        return self.aid(it.get("IncomeAccountRef"))

    def tax_lines(self, t, ccy, rate, debit: bool) -> list[dict[str, Any]]:
        tax = _d((t.get("TxnTaxDetail") or {}).get("TotalTax"))
        if not tax:
            return []
        acct = self.special("tax_payable")
        if not acct:
            self.warns.append(f"{t.get('Id')}: tax {tax} but no tax-payable account in the chart")
            return []
        return [self.line(acct, debit=tax) if debit else self.line(acct, credit=tax)]

    # -- sales side
    def invoice(self, t: dict[str, Any]) -> None:
        total = _d(t["TotalAmt"])
        cust = self.parties.get(("Customer", str((t.get("CustomerRef") or {}).get("value"))))
        ccy, rate = self.money(t)
        ar = self.aid(t.get("ARAccountRef")) or self.special("ar")
        lines = [self.line(ar, debit=total, cp=cust)]
        for L in t.get("Line", []):
            dt_ = L.get("DetailType")
            if dt_ == "SalesItemLineDetail":
                acct = self.aid(L["SalesItemLineDetail"].get("ItemAccountRef")) or \
                    self.item_account(L["SalesItemLineDetail"].get("ItemRef"), expense=False) or self.special("revenue")
                lines.append(self.line(acct, credit=_d(L["Amount"])))
            elif dt_ == "DiscountLineDetail":
                acct = self.aid(L["DiscountLineDetail"].get("DiscountAccountRef")) or self.special("revenue")
                lines.append(self.line(acct, debit=_d(L["Amount"])))
        lines += self.tax_lines(t, ccy, rate, debit=False)
        je = self.add("Invoice", t, f"Invoice {t.get('DocNumber', '')} - {(t.get('CustomerRef') or {}).get('name', '')}", lines)
        bal = _d(t.get("Balance"))
        status = "paid" if bal == 0 and total > 0 else ("partially_paid" if bal < total else "open")
        self.invoices.append({
            "id": make_id("inv", self.cid, "qbo", t["Id"]), "company_id": self.cid, "counterparty_id": cust,
            "invoice_no": clean(t.get("DocNumber")), "issue_date": _date(t.get("TxnDate")), "due_date": _date(t.get("DueDate")),
            "currency": ccy, "fx_rate": str(rate), "total": money_str(total),
            "tax": money_str(_d((t.get("TxnTaxDetail") or {}).get("TotalTax"))), "total_base": money_str(total * rate),
            "status": status, "journal_entry_id": je, "source_system": SYSTEM, "source_file": "qbo:Invoice",
            "source_row_id": str(t["Id"])})

    def bill(self, t: dict[str, Any]) -> None:
        total = _d(t["TotalAmt"])
        sup = self.parties.get(("Vendor", str((t.get("VendorRef") or {}).get("value"))))
        ccy, rate = self.money(t)
        ap = self.aid(t.get("APAccountRef")) or self.special("ap")
        lines, main = [], None
        for L in t.get("Line", []):
            if L.get("DetailType") == "AccountBasedExpenseLineDetail":
                acct = self.aid(L["AccountBasedExpenseLineDetail"].get("AccountRef"))
            elif L.get("DetailType") == "ItemBasedExpenseLineDetail":
                acct = self.item_account(L["ItemBasedExpenseLineDetail"].get("ItemRef"), expense=True)
            else:
                continue
            acct = acct or self.special("opex")
            main = main or acct
            lines.append(self.line(acct, debit=_d(L["Amount"])))
        lines += self.tax_lines(t, ccy, rate, debit=True)
        lines.append(self.line(ap, credit=total, cp=sup))
        je = self.add("Bill", t, f"Bill {t.get('DocNumber', '')} - {(t.get('VendorRef') or {}).get('name', '')}", lines)
        bal = _d(t.get("Balance"))
        self.bills.append({
            "id": make_id("bill", self.cid, "qbo", t["Id"]), "company_id": self.cid, "counterparty_id": sup,
            "bill_no": clean(t.get("DocNumber")), "issue_date": _date(t.get("TxnDate")), "due_date": _date(t.get("DueDate")),
            "currency": ccy, "fx_rate": str(rate), "total": money_str(total),
            "tax": money_str(_d((t.get("TxnTaxDetail") or {}).get("TotalTax"))), "total_base": money_str(total * rate),
            "status": "paid" if bal == 0 and total > 0 else ("partially_paid" if bal < total else "open"),
            "expense_account_id": main, "journal_entry_id": je, "source_system": SYSTEM, "source_file": "qbo:Bill",
            "source_row_id": str(t["Id"])})

    # -- cash side
    def payment(self, t: dict[str, Any]) -> None:
        total = _d(t["TotalAmt"])
        cust = self.parties.get(("Customer", str((t.get("CustomerRef") or {}).get("value"))))
        ccy, rate = self.money(t)
        dep = self.aid(t.get("DepositToAccountRef")) or self.special("bank")
        je = self.add("Payment", t, f"Customer payment {t.get('PaymentRefNum', '')}".strip(),
                      [self.line(dep, debit=total), self.line(self.special("ar"), credit=total, cp=cust)])
        for L in t.get("Line", []):
            for lk in L.get("LinkedTxn", []):
                if lk.get("TxnType") == "Invoice":
                    self.inv_pay.append({
                        "id": make_id("ipay", self.cid, "qbo", t["Id"], lk["TxnId"]), "company_id": self.cid,
                        "invoice_id": make_id("inv", self.cid, "qbo", lk["TxnId"]), "payment_date": _date(t.get("TxnDate")),
                        "amount": money_str(_d(L["Amount"])), "currency": ccy, "fx_rate": str(rate),
                        "amount_base": money_str(_d(L["Amount"]) * rate), "method": "bank", "deposit_account_id": dep,
                        "journal_entry_id": je, "source_system": SYSTEM, "source_file": "qbo:Payment",
                        "source_row_id": f"{t['Id']}#{lk['TxnId']}"})

    def bill_payment(self, t: dict[str, Any]) -> None:
        total = _d(t["TotalAmt"])
        sup = self.parties.get(("Vendor", str((t.get("VendorRef") or {}).get("value"))))
        ccy, rate = self.money(t)
        src = self.aid(((t.get("CheckPayment") or {}).get("BankAccountRef")) or ((t.get("CreditCardPayment") or {}).get("CCAccountRef"))) \
            or self.special("bank")
        je = self.add("BillPayment", t, f"Supplier payment {t.get('DocNumber', '')}".strip(),
                      [self.line(self.special("ap"), debit=total, cp=sup), self.line(src, credit=total)])
        for L in t.get("Line", []):
            for lk in L.get("LinkedTxn", []):
                if lk.get("TxnType") == "Bill":
                    self.bill_pay.append({
                        "id": make_id("bpay", self.cid, "qbo", t["Id"], lk["TxnId"]), "company_id": self.cid,
                        "bill_id": make_id("bill", self.cid, "qbo", lk["TxnId"]), "payment_date": _date(t.get("TxnDate")),
                        "amount": money_str(_d(L["Amount"])), "currency": ccy, "fx_rate": str(rate),
                        "amount_base": money_str(_d(L["Amount"]) * rate), "method": "bank", "paid_from_account_id": src,
                        "journal_entry_id": je, "source_system": SYSTEM, "source_file": "qbo:BillPayment",
                        "source_row_id": f"{t['Id']}#{lk['TxnId']}"})

    def purchase(self, t: dict[str, Any]) -> None:
        """Expense / cheque / card purchase paid immediately: Dr expense lines, Cr the paying account."""
        total = _d(t["TotalAmt"])
        lines = []
        for L in t.get("Line", []):
            if L.get("DetailType") == "AccountBasedExpenseLineDetail":
                lines.append(self.line(self.aid(L["AccountBasedExpenseLineDetail"].get("AccountRef")) or self.special("opex"),
                                       debit=_d(L["Amount"])))
        lines += self.tax_lines(t, *self.money(t), debit=True)
        lines.append(self.line(self.aid(t.get("AccountRef")) or self.special("bank"), credit=total))
        self.add("Purchase", t, f"Expense {t.get('DocNumber', '') or t.get('Id')}", lines)

    def sales_receipt(self, t: dict[str, Any]) -> None:
        total = _d(t["TotalAmt"])
        lines = [self.line(self.aid(t.get("DepositToAccountRef")) or self.special("bank"), debit=total)]
        for L in t.get("Line", []):
            if L.get("DetailType") == "SalesItemLineDetail":
                lines.append(self.line(self.item_account(L["SalesItemLineDetail"].get("ItemRef"), expense=False)
                                       or self.special("revenue"), credit=_d(L["Amount"])))
        lines += self.tax_lines(t, *self.money(t), debit=False)
        self.add("SalesReceipt", t, f"Sales receipt {t.get('DocNumber', '') or t.get('Id')}", lines)

    def journal(self, t: dict[str, Any]) -> None:
        lines = []
        for L in t.get("Line", []):
            det = L.get("JournalEntryLineDetail")
            if not det:
                continue
            amt = _d(L["Amount"])
            ent = (det.get("Entity") or {}).get("EntityRef") or {}
            cp = self.parties.get(("Customer", str(ent.get("value")))) or self.parties.get(("Vendor", str(ent.get("value"))))
            ln = self.line(self.aid(det.get("AccountRef")), debit=amt if det.get("PostingType") == "Debit" else ZERO,
                           credit=amt if det.get("PostingType") == "Credit" else ZERO, cp=cp)
            ln["memo"] = clean(L.get("Description"))
            lines.append(ln)
        self.add("JournalEntry", t, clean(t.get("PrivateNote")) or f"Journal entry {t.get('DocNumber', '')}", lines)


if __name__ == "__main__":                       # python -m backend.connectors.qbo authorize
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "authorize":
        from backend.connectors._qbo_client import run_local_callback
        _cfg = QboConfig()
        _miss = [k for k, v in (("QBO_CLIENT_ID", _cfg.client_id), ("QBO_CLIENT_SECRET", _cfg.client_secret)) if not v]
        if _miss:
            raise SystemExit("set " + ", ".join(_miss) + " in backend/.env first")
        run_local_callback(_cfg, QboClient(_cfg))
    else:
        print("usage: python -m backend.connectors.qbo authorize")
