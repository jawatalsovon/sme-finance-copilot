"""Cash Truth: what the books say about cash vs. what can be verified (statements, counts).

Pipeline (plain words, also in backend/engine/README.md):
  1. Balances  - book balance of every cash/bank/wallet account vs. the statement balance or the
                 latest physical cash count on or before the as-of date. Gap = verified - book.
  2. Matching  - every statement-backed account is matched line by line (matching.py).
                 Exceptions become findings: unrecorded payments/receipts/fees, duplicates,
                 timing and FX differences.
  3. Settlements - marketplace settlement reports are reconciled bucket by bucket (sales,
                 refunds, fees, reimbursements, reserve, payout) against the clearing account.
  4. Cash counts - the till/safe has no statement; between two counts we compare the counted
                 change with the booked change, after removing what the other findings explain.
  5. Book checks - patterns visible in the books alone: goods paid twice (advance + cash),
                 entries posted to an unusual account for their description, FX rates far from
                 the market rate, receivables nobody pays any more.
Every finding carries the source rows as evidence and, when a correction is warranted, a
balanced draft journal entry. Nothing is written: drafts need the owner's approval (T2).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backend.core.util import format_money
from backend.engine.data import (
    CASH_SUBTYPES, Ledger, dec, is_fee_account_name, is_small_expense_account_name, month_key,
)
from backend.engine.matching import book_items, match_account
from backend.engine.types import (
    CashTruthResult, ChannelBalance, DraftEntryLine, DraftJournalEntry, Figure, Finding,
)
from backend.ledger.lineage import make_ref

STALE_DAYS = 90            # receivable past due this long -> stale
PHANTOM_DAYS = 180         # customer owing money with no payment for this long -> likely uncollectable
FX_RATE_TOLERANCE = 0.0075 # booked rate vs market rate
MAX_EVIDENCE = 200


# ---------------------------------------------------------------------------- raw findings
@dataclass
class Raw:
    kind: str
    category: str                       # finer label (also used for grouping and scoring)
    account_id: str | None
    date: dt.date
    impact_c: int                       # effect on (verified - book) of this account, native cents
    currency: str
    title: str
    explanation: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    entries: list[DraftJournalEntry] = field(default_factory=list)
    affects_cash: bool = True           # counts towards the cash gap
    base_c: int | None = None           # impact in base currency (computed later if None)
    pnl: list[tuple[str, str, int]] = field(default_factory=list)   # (month, account_id, base cents debit+)


def _refs(table: str, ids) -> list[str]:
    return [make_ref(table, str(i)) for i in ids]


def _cut(xs: list[str]) -> list[str]:
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _stem(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b", " ", t)
    t = re.sub(r"[0-9]+", " ", t)
    t = re.sub(r"[^a-z&]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower())
            if w not in {"the", "and", "for", "ltd", "co", "llc", "gmbh", "sarl", "srl", "inc", "company"}}


# ---------------------------------------------------------------------------- engine
class CashTruthRun:
    def __init__(self, L: Ledger, as_of: dt.date):
        self.L = L
        self.as_of = as_of
        self.base = L.base
        self.raws: list[Raw] = []
        self.lines = L.lines_upto(as_of)
        st = L.statements
        self.stmt = st[st["txn_date"] <= as_of]
        self.acc = L.accounts
        self.cash_accounts = self.acc[self.acc["subtype"].isin(CASH_SUBTYPES)]
        # learned helpers
        self._entry_lines = {k: v for k, v in self.lines.groupby("entry_id")} if len(self.lines) else {}
        self.cash_adjust: dict[str, list[tuple[dt.date, int, str]]] = defaultdict(list)  # cash acct -> (date, cents, why)
        self.bank_reality_shift: dict[str, list[tuple[dt.date, dt.date, int]]] = defaultdict(list)

    # -------------------------------------------------------------- account helpers
    def _first(self, mask) -> str | None:
        a = self.acc[mask]
        return a.index[0] if len(a) else None

    def acct_by_subtype(self, *subtypes: str, name_re: str | None = None) -> str | None:
        m = self.acc["subtype"].isin(subtypes)
        if name_re:
            mm = m & self.acc["name"].str.contains(name_re, case=False, regex=True)
            if mm.any():
                return self.acc[mm].index[0]
        return self._first(m)

    def counter_accounts(self, entry_id: str, exclude: str) -> list[str]:
        g = self._entry_lines.get(entry_id)
        if g is None:
            return []
        return [a for a in g["account_id"].tolist() if a != exclude]

    def fee_account_for(self, account_id: str) -> str | None:
        """Account the owner normally uses for charges on this cash account (learned from the books)."""
        lines = self.lines
        mine = lines[lines["account_id"] == account_id]["entry_id"]
        other = lines[lines["entry_id"].isin(mine) & (lines["account_id"] != account_id) &
                      (lines["acc_type"] == "expense")]
        fee = other[other["acc_name"].map(is_fee_account_name)]
        if len(fee):
            return Counter(fee["account_id"]).most_common(1)[0][0]
        return self.acct_by_subtype("opex", name_re=r"charge|fee|commission") or self.acct_by_subtype("opex")

    def small_expense_account(self) -> str | None:
        m = (self.acc["type"] == "expense") & self.acc["name"].map(is_small_expense_account_name)
        return self._first(m) or self.acct_by_subtype("opex", "other_expense")

    # -------------------------------------------------------------- draft entries
    def draft(self, date: dt.date, memo: str, reason: str, legs: list[tuple[str, int, str, float]],
              evidence: list[str]) -> DraftJournalEntry | None:
        """legs: (account_id, signed native cents debit+, currency, fx_rate). Balanced in base by
        construction of the callers (a final base-currency leg absorbs rounding)."""
        if not legs or any(a is None for a, *_ in legs):
            return None
        lines = []
        total_base = 0
        for a, c, ccy, rate in legs:
            if c == 0:
                continue
            b = int(round(c * rate))
            total_base += b
            lines.append(DraftEntryLine(account_id=a, debit=dec(c) if c > 0 else dec(0),
                                        credit=dec(-c) if c < 0 else dec(0), currency=ccy,
                                        fx_rate=round(float(rate), 8), amount_base=dec(b)))
        if total_base != 0:  # FX rounding / translation: book the remainder to FX gain/loss
            fx_acc = self.acct_by_subtype("fx_loss") or legs[-1][0]
            lines.append(DraftEntryLine(account_id=fx_acc, debit=dec(-total_base) if total_base < 0 else dec(0),
                                        credit=dec(total_base) if total_base > 0 else dec(0), currency=self.base,
                                        fx_rate=1.0, amount_base=dec(-total_base), memo="currency difference"))
        if len(lines) < 2:
            return None
        deb = sum(int(round(float(ln.amount_base) * 100)) for ln in lines if ln.amount_base > 0)
        return DraftJournalEntry(entry_date=date, memo=memo[:200], lines=lines, reason=reason[:500],
                                 currency_base=self.base, total_debit_base=dec(deb), total_credit_base=dec(deb),
                                 evidence=evidence[:MAX_EVIDENCE])

    def rate(self, ccy: str, on: dt.date) -> float:
        r = self.L.fx_rate(ccy, on)
        return 1.0 if r is None else r

    def add(self, raw: Raw) -> None:
        self.raws.append(raw)

    # ================================================================== 1. statement accounts
    def run_statement_accounts(self) -> None:
        st = self.stmt
        accounts = st[st["channel"].isin(["bank", "wallet"])]["account_id"].dropna().unique().tolist()
        self.match_cache = {}
        for acc in sorted(accounts):
            self._statement_account(acc)

    def _fee_convention(self, items: pd.DataFrame, s: pd.DataFrame) -> str:
        """Does the statement amount include the fee ('amount') or sit next to it ('amount+fee')?
        Pick the reading that lets more lines match exactly on day and amount."""
        if (s["fee_c"] == 0).all():
            return "amount"
        keys_b = Counter(zip(items["date"], items["amt"]))
        def score(vals):
            c = Counter(zip(s["txn_date"], vals))
            return sum(min(v, keys_b.get(k, 0)) for k, v in c.items())
        return "amount+fee" if score(s["amount_c"] + s["fee_c"]) > score(s["amount_c"]) else "amount"

    def _statement_account(self, acc: str) -> None:
        ccy = self.acc.loc[acc, "currency"] if acc in self.acc.index else self.base
        lines = self.lines[self.lines["account_id"] == acc]
        s = self.stmt[(self.stmt["account_id"] == acc) & self.stmt["channel"].isin(["bank", "wallet"])].reset_index(drop=True)
        items = book_items(lines)
        conv = self._fee_convention(items, s)
        fee_entries: set[str] = set()
        if conv == "amount+fee":
            # fees are separate deductions on this statement: the books record them as charge entries
            other = self.lines[self.lines["entry_id"].isin(items["entry_id"]) & (self.lines["account_id"] != acc)]
            fee_entries = set(other[other["acc_name"].map(is_fee_account_name)]["entry_id"])
            principal = s["amount_c"] + s["fee_c"]
        else:
            principal = s["amount_c"]
        work_items = items[~items["entry_id"].isin(fee_entries)].reset_index(drop=True)
        sm = pd.DataFrame({"id": s["id"], "date": s["txn_date"], "amt": principal})
        r = match_account(work_items, sm)
        self.match_cache[acc] = (r, s, work_items)
        b, sx = r.book, r.stmt

        # --- timing differences (matched, different dates) -> group per month
        w = b[b["m_kind"] == "window"]
        if len(w):
            w = w.assign(sdate=sx.loc[w["m_stmt"].astype(int), "date"].to_numpy(),
                         sid=sx.loc[w["m_stmt"].astype(int), "id"].to_numpy())
            for month, g in w.groupby("month"):
                n = len(g)
                tot = int(g["amt"].abs().sum())
                days = sorted({abs((sd - bd).days) for sd, bd in zip(g["sdate"], g["date"])})
                ev = _refs("journal_entries", g["entry_id"]) + _refs("external_statement_lines", g["sid"])
                self.add(Raw("timing_difference", "timing", acc, max(g["date"]), 0, ccy,
                             f"{n} entr{'y' if n == 1 else 'ies'} dated {'/'.join(map(str, days))} day(s) away from the statement in {month}",
                             f"In {month}, {n} item(s) totalling {format_money(dec(tot), ccy)} on {self.L.account_name(acc)} "
                             f"were booked on a different day than the bank/wallet shows. The money is the same, "
                             f"only the date differs; month-end balances and reports for {month} are affected.",
                             0.9, ev, affects_cash=False))
                for _, row in g.iterrows():
                    for other in self.counter_accounts(row["entry_id"], acc):
                        if other in self.cash_accounts.index:
                            # the other side (e.g. the till) really moved on the statement date
                            amt_other = int(self._entry_lines[row["entry_id"]].query("account_id == @other")["native_c"].sum())
                            self.bank_reality_shift[other].append((row["date"], row["sdate"], amt_other))

        # --- amount differences (near pairs)
        for ib, is_ in r.near_pairs:
            bi, si = b.loc[ib], sx.loc[is_]
            srow = s.loc[is_]
            diff = int(si["amt"] - bi["amt"])          # verified - book, native
            self._amount_difference(acc, ccy, bi, srow, diff)

        # --- statement lines the books never recorded
        us = s.loc[sx[sx["m_kind"] == ""].index]
        ub = b[b["m_kind"] == ""]
        self._unrecorded_statement_lines(acc, ccy, us, principal)
        self._unconfirmed_book_items(acc, ccy, ub)

        # --- fees on statements that report them separately
        if conv == "amount+fee":
            self._fee_reconciliation(acc, ccy, s, items[items["entry_id"].isin(fee_entries)])

    # -------------------------------------------------------------- near pairs
    def _amount_difference(self, acc: str, ccy: str, bi, srow, diff: int) -> None:
        entry = bi["entry_id"]
        others = self.counter_accounts(entry, acc)
        other_types = [self.acc.loc[o, "subtype"] if o in self.acc.index else "" for o in others]
        g = self._entry_lines.get(entry)
        foreign_leg = g is not None and (g["currency"] != ccy).any()
        ev = _refs("journal_entries", [entry]) + _refs("external_statement_lines", [srow["id"]])
        date = srow["txn_date"]
        desc = srow["description"]
        if "loan" in other_types and diff < 0:
            cat, kind = "loan_interest", "unrecorded_expense"
        elif any(t in CASH_SUBTYPES for t in other_types) and foreign_leg:
            cat, kind = "fx_conversion", "fx_difference"
        elif any(t == "ar" for t in other_types) and re.search(r"payout|settlement|deposit", f"{desc} {bi['memo']}", re.I):
            cat, kind = "payout_short" if diff < 0 else "payout_over", "fee_not_recorded" if diff < 0 else "other"
        elif foreign_leg:
            cat, kind = "fx_other", "fx_difference"
        else:
            cat, kind = "amount_diff", "other"
        self.add(Raw(kind, cat, acc, date, diff, ccy, "", f"{desc}|{bi['memo']}", 0.8, ev,
                     entries=[], affects_cash=True))
        self.raws[-1].pnl_hint = (entry, others)  # type: ignore[attr-defined]

    # -------------------------------------------------------------- unrecorded statement lines
    def _unrecorded_statement_lines(self, acc: str, ccy: str, us: pd.DataFrame, principal: pd.Series) -> None:
        if us.empty:
            return
        us = us.assign(p=principal.loc[us.index].to_numpy())
        # (a) receipts typed into another cash account on the same day (e.g. wallet sales booked as cash)
        inflow = us[us["p"] > 0]
        consumed: set[int] = set()
        if len(inflow):
            other_items = self.lines[(self.lines["account_id"].isin(self.cash_accounts.index)) &
                                     (self.lines["account_id"] != acc) & (self.lines["native_c"] > 0)]
            other_by_day = defaultdict(list)
            for rr in other_items.itertuples():
                other_by_day[rr.entry_date].append(rr)
            for day, g in inflow.groupby("txn_date"):
                tot = int(g["p"].sum())
                cands = [o for o in other_by_day.get(day, []) if int(o.native_c) == tot and
                         not self._entry_has_statement_support(o.entry_id, o.account_id)]
                if not cands:
                    continue
                o = cands[0]
                consumed.update(g.index)
                self.cash_adjust[o.account_id].append((day, -tot, f"misrouted:{o.entry_id}"))
                ev = _refs("journal_entries", [o.entry_id]) + _refs("external_statement_lines", g["id"])
                rate = self.rate(ccy, day)
                e = self.draft(day, f"Move receipts of {day} from {self.L.account_name(o.account_id)} to {self.L.account_name(acc)}",
                               "The statement shows these receipts arrived in this account, but the books put them in another cash account.",
                               [(acc, tot, ccy, rate), (o.account_id, -tot, self.acc.loc[o.account_id, "currency"], rate)], ev)
                self.add(Raw("other", "misrouted_receipt", acc, day, tot, ccy,
                             f"Receipts of {day} booked to the wrong account",
                             f"{len(g)} receipt(s) totalling {format_money(dec(tot), ccy)} arrived in {self.L.account_name(acc)} "
                             f"on {day}, but the books recorded the same total in {self.L.account_name(o.account_id)}. "
                             f"Sales are right; the cash is in the wrong place.", 0.9, ev,
                             [e] if e else [], affects_cash=True))
                self.raws[-1].extra_cash = (o.account_id, -tot)  # type: ignore[attr-defined]

        rest = us.drop(index=list(consumed))
        if rest.empty:
            return
        supplier_names = self._names("supplier")
        customer_names = self._names("customer")
        open_ar = self._open_ar_by_customer()
        # (b) money out
        out = rest[rest["p"] < 0]
        personal_like = []
        for idx, row in out.iterrows():
            cp, desc = row["counterparty_text"], row["description"]
            p = int(row["p"])
            if re.search(r"\b(charges?|fees?|commissions?)\b", desc, re.I):
                self._unrecorded_out(acc, ccy, row, p, "fee_not_recorded", "fee", self.fee_account_for(acc),
                                     f"Charge of {format_money(dec(-p), ccy)} on {row['txn_date']} not in your books")
                continue
            sup = self._best_name(cp + " " + desc, supplier_names)
            if sup is not None:
                ap = self.acct_by_subtype("ap") or self.acct_by_subtype("inventory")
                self._unrecorded_out(acc, ccy, row, p, "unrecorded_expense", "supplier_payment", ap,
                                     f"Payment of {format_money(dec(-p), ccy)} to {sup} on {row['txn_date']} not in your books")
                continue
            personal_like.append(idx)
        if personal_like:
            g_all = out.loc[personal_like]
            for month, g in g_all.groupby("month"):
                tot = int(g["p"].sum())
                n = len(g)
                ev = _refs("external_statement_lines", g["id"])
                draw = self.acct_by_subtype("drawings") or self.acct_by_subtype("owner_equity")
                rate = self.rate(ccy, max(g["txn_date"]))
                e = self.draft(max(g["txn_date"]), f"Payments to private/unknown recipients {month}",
                               "Money left the account to recipients that are not known suppliers; if these were the "
                               "owner's own payments they are drawings, otherwise tell us what they were for.",
                               [(draw, -tot, ccy, rate), (acc, tot, ccy, rate)], ev)
                self.add(Raw("unrecorded_expense", "unknown_payee", acc, max(g["txn_date"]), tot, ccy,
                             f"{n} payment(s) in {month} to unknown recipients, not in your books",
                             f"In {month}, {n} payment(s) totalling {format_money(dec(-tot), ccy)} left "
                             f"{self.L.account_name(acc)} to recipients that are not known suppliers "
                             f"(e.g. '{g['counterparty_text'].iloc[0] or g['description'].iloc[0]}'). "
                             f"None of them is in the books. If they were personal, record them as owner's drawings.",
                             0.75, ev, [e] if e else []))
        # (c) money in
        inn = rest[rest["p"] > 0]
        for idx, row in inn.iterrows():
            p = int(row["p"])
            cust = self._best_name(row["counterparty_text"] + " " + row["description"], customer_names)
            ar_acc = None
            if cust is not None and cust in open_ar:
                ar_acc = open_ar[cust][1]
            elif open_ar:
                ar_acc = self._default_ar(ccy)
            target = ar_acc or self.acct_by_subtype("other_income") or self.acct_by_subtype("revenue")
            who = cust or (row["counterparty_text"] or "a payer")
            rate = self.rate(ccy, row["txn_date"])
            ev = _refs("external_statement_lines", [row["id"]])
            why = ("This looks like a customer paying what they owed, so it reduces receivables."
                   if ar_acc else "No open receivable fits, so it is proposed as other income; tell us what it was.")
            tgt_ccy = self.acc.loc[target, "currency"] if target in self.acc.index else self.base
            tgt_rate = rate if tgt_ccy == ccy else 1.0
            tgt_amt = p if tgt_ccy == ccy else int(round(p * rate))
            e = self.draft(row["txn_date"], f"Receipt from {who} {row['txn_date']}", why,
                           [(acc, p, ccy, rate), (target, -tgt_amt, tgt_ccy, tgt_rate)], ev)
            self.add(Raw("unrecorded_receipt", "receipt", acc, row["txn_date"], p, ccy,
                         f"{format_money(dec(p), ccy)} received on {row['txn_date']} is not in your books",
                         f"The statement of {self.L.account_name(acc)} shows {format_money(dec(p), ccy)} coming in on "
                         f"{row['txn_date']} ('{row['description']}', from {who}). The books have no matching entry. {why}",
                         0.85, ev, [e] if e else []))
            self.raws[-1].ar_credit = (target, cust)  # type: ignore[attr-defined]

    def _unrecorded_out(self, acc, ccy, row, p, kind, cat, debit_acc, title):
        rate = self.rate(ccy, row["txn_date"])
        ev = _refs("external_statement_lines", [row["id"]])
        tgt_ccy = self.acc.loc[debit_acc, "currency"] if debit_acc in self.acc.index else self.base
        tgt_rate, tgt_amt = (rate, -p) if tgt_ccy == ccy else (1.0, int(round(-p * rate)))
        e = self.draft(row["txn_date"], title, "The statement shows this payment; the books do not.",
                       [(debit_acc, tgt_amt, tgt_ccy, tgt_rate), (acc, p, ccy, rate)], ev)
        self.add(Raw(kind, cat, acc, row["txn_date"], p, ccy, title,
                     f"{self.L.account_name(acc)} shows '{row['description']}' ({row['counterparty_text']}) of "
                     f"{format_money(dec(-p), ccy)} on {row['txn_date']}; no book entry matches it.", 0.85, ev,
                     [e] if e else []))

    def _entry_has_statement_support(self, entry_id: str, account_id: str) -> bool:
        cache = getattr(self, "match_cache", {})
        if account_id not in cache:
            return False
        r, _, _ = cache[account_id]
        m = r.book[r.book["entry_id"] == entry_id]
        return bool(len(m)) and (m["m_kind"] != "").all()

    # -------------------------------------------------------------- book items without statement support
    def _unconfirmed_book_items(self, acc: str, ccy: str, ub: pd.DataFrame) -> None:
        if ub.empty:
            return
        r, _, items = self.match_cache[acc]
        matched = r.book[r.book["m_kind"] != ""]
        for _, it in ub.iterrows():
            # twin: same amount, similar memo, within 31 days, and the twin IS on the statement
            near = matched[(matched["amt"] == it["amt"]) &
                           (pd.to_datetime(matched["date"]).sub(pd.Timestamp(it["date"])).dt.days.abs() <= 31)]
            twin = None
            tk = _tokens(it["memo"])
            for _, m in near.iterrows():
                mk = _tokens(m["memo"])
                if tk and mk and len(tk & mk) / max(1, min(len(tk), len(mk))) >= 0.5:
                    twin = m
                    break
            entry = it["entry_id"]
            ev = _refs("journal_entries", [entry])
            amt = int(it["amt"])
            if twin is not None:
                ev = ev + _refs("journal_entries", [twin["entry_id"]])
                e = self._reverse_entry(entry, f"Reverse duplicate of {twin['reference'] or twin['entry_id']}",
                                        "The same payment is in the books twice but only once on the statement.", ev)
                self.add(Raw("duplicate_entry", "duplicate", acc, it["date"], -amt, ccy,
                             f"'{it['memo']}' is booked twice",
                             f"{format_money(dec(abs(amt)), ccy)} '{it['memo']}' on {it['date']} appears twice in the books "
                             f"(also on {twin['date']}), but {self.L.account_name(acc)} shows it only once.", 0.9, ev,
                             [e] if e else []))
            else:
                self.add(Raw("other", "unconfirmed_book", acc, it["date"], -amt, ccy,
                             f"Book entry of {format_money(dec(abs(amt)), ccy)} on {it['date']} not on the statement",
                             f"'{it['memo']}' ({format_money(dec(amt), ccy)}) on {self.L.account_name(acc)} has no matching "
                             f"statement line. It may be a mistake, or the money has not cleared yet.", 0.5, ev))

    def _reverse_entry(self, entry_id: str, memo: str, reason: str, ev: list[str]) -> DraftJournalEntry | None:
        g = self._entry_lines.get(entry_id)
        if g is None:
            return None
        legs = [(r.account_id, -int(r.native_c), r.currency, float(r.fx_rate)) for r in g.itertuples()]
        d = self.draft(g["entry_date"].iloc[0], memo, reason, legs, ev)
        return d

    # -------------------------------------------------------------- fees reported next to the amount
    def _fee_reconciliation(self, acc: str, ccy: str, s: pd.DataFrame, fee_items: pd.DataFrame) -> None:
        sf = s[s["fee_c"] != 0].groupby("month").agg(fee=("fee_c", "sum"), n=("id", "size"))
        bf = fee_items.groupby("month")["amt"].sum() if len(fee_items) else pd.Series(dtype="int64")
        fee_acc = self.fee_account_for(acc)
        for month, row in sf.iterrows():
            booked = int(-bf.get(month, 0))
            missing = int(row["fee"]) - booked
            if missing <= max(100, int(0.01 * row["fee"])):  # within 1 unit / 1%
                continue
            ids = s[(s["month"] == month) & (s["fee_c"] != 0)]["id"]
            ev = _refs("external_statement_lines", ids)
            if len(fee_items):
                ev += _refs("journal_entries", fee_items[fee_items["month"] == month]["entry_id"])
            last = max(s[s["month"] == month]["txn_date"])
            rate = self.rate(ccy, last)
            e = self.draft(last, f"{self.L.account_name(acc)} charges {month}",
                           "The statement deducted these charges; the books do not show them.",
                           [(fee_acc, missing, ccy, rate), (acc, -missing, ccy, rate)], ev)
            self.add(Raw("fee_not_recorded", "statement_fees", acc, last, -missing, ccy,
                         f"{format_money(dec(missing), ccy)} of charges in {month} not in your books",
                         f"{self.L.account_name(acc)} deducted {format_money(dec(int(row['fee'])), ccy)} of charges on "
                         f"{int(row['n'])} transactions in {month}; the books record {format_money(dec(booked), ccy)}.",
                         0.9, ev, [e] if e else []))

    # -------------------------------------------------------------- names
    def _names(self, kind: str) -> dict[str, set[str]]:
        cp = self.L.counterparties
        out = {}
        for r in cp[cp["kind"] == kind].itertuples():
            t = _tokens(r.name)
            if t:
                out[r.name] = t
        return out

    @staticmethod
    def _best_name(text: str, names: dict[str, set[str]]) -> str | None:
        tk = _tokens(text)
        if not tk:
            return None
        best, score = None, 0.0
        for n, nt in names.items():
            s = len(tk & nt) / len(nt)
            if s > score:
                best, score = n, s
        return best if score >= 0.6 else None

    def _open_ar_by_customer(self) -> dict[str, tuple[int, str]]:
        inv = self.L.invoices
        if inv.empty:
            return {}
        inv = inv[inv["issue_date"] <= self.as_of]
        out_c = (pd.to_numeric(inv["total"]) - pd.to_numeric(inv["paid"])).round(2)
        inv = inv.assign(out=out_c)
        res = {}
        ar_acc = self._invoice_ar_accounts()
        for name, g in inv[inv["out"] > 0.005].groupby("customer_name"):
            acc = ar_acc.get(g["journal_entry_id"].dropna().iloc[0]) if g["journal_entry_id"].notna().any() else None
            res[name] = (int(round(g["out"].sum() * 100)), acc or self._default_ar(g["currency"].iloc[0]))
        return res

    def _invoice_ar_accounts(self) -> dict[str, str]:
        if hasattr(self, "_inv_ar"):
            return self._inv_ar
        l = self.lines
        ar = l[l["acc_subtype"] == "ar"]
        self._inv_ar = dict(zip(ar["entry_id"], ar["account_id"]))
        return self._inv_ar

    def _default_ar(self, ccy: str) -> str | None:
        inv = self.L.invoices
        ar_map = self._invoice_ar_accounts()
        if len(inv):
            accs = [ar_map.get(e) for e in inv[inv["currency"] == ccy]["journal_entry_id"].dropna()]
            accs = [a for a in accs if a]
            if accs:
                return Counter(accs).most_common(1)[0][0]
        return self.acct_by_subtype("ar")

    # ================================================================== 2. marketplace settlements
    def run_settlements(self) -> None:
        st = self.stmt[self.stmt["channel"] == "pos_settlement"]
        for acc, s in st.groupby("account_id"):
            if acc in self.cash_accounts.index:
                self._payout_report(acc, s)        # e.g. card processor deposits listed per payout
            else:
                self._settlement_account(acc, s)

    def _payout_report(self, cash_acc: str, rep: pd.DataFrame) -> None:
        """A processor's deposit report (gross, fee, net per payout) mapped to the bank account.
        The fees must appear in the books as an expense taken from the clearing account the
        sales were booked to; month by month we compare report fees with booked fees."""
        rep = rep[rep["fee_c"] != 0]
        if rep.empty or cash_acc not in self.match_cache:
            return
        r, s, items = self.match_cache[cash_acc]
        # clearing account = the other side of the book entries that match the payouts on the bank
        keys = Counter(zip(rep["txn_date"], rep["amount_c"]))
        hits = items.loc[np.array([(d, a) in keys for d, a in zip(items["date"], items["amt"])], dtype=bool)]
        cnt = Counter()
        for e in hits["entry_id"]:
            for o in self.counter_accounts(e, cash_acc):
                cnt[o] += 1
        if not cnt:
            return
        clearing = cnt.most_common(1)[0][0]
        ccy = self.acc.loc[clearing, "currency"]
        l = self.lines
        ents = l[l["account_id"] == clearing]["entry_id"]
        fee_lines = l[l["entry_id"].isin(ents) & (l["acc_type"] == "expense") & l["acc_name"].map(is_fee_account_name)]
        booked = fee_lines.groupby("month")["native_c"].sum()
        fee_acc = Counter(fee_lines["account_id"]).most_common(1)[0][0] if len(fee_lines) else self.fee_account_for(cash_acc)
        for month, g in rep.groupby("month"):
            fee = int(g["fee_c"].sum())
            missing = fee - int(booked.get(month, 0))
            if missing <= max(100, fee // 100):
                continue
            last = max(g["txn_date"])
            ev = _refs("external_statement_lines", g["id"]) + _refs("journal_entries", hits[hits["month"] == month]["entry_id"])
            rate = self.rate(ccy, last)
            e = self.draft(last, f"Card processing fees {month}",
                           "The processor's deposit report shows these fees deducted from your card sales; the books "
                           "do not show them, so the clearing account never clears.",
                           [(fee_acc, missing, ccy, rate), (clearing, -missing, ccy, rate)], ev)
            self.add(Raw("fee_not_recorded", "processor_fees", clearing, last, -missing, ccy,
                         f"{format_money(dec(missing), ccy)} card processing fees in {month} not in your books",
                         f"In {month}, {len(g)} payout(s) to {self.L.account_name(cash_acc)} arrived net of "
                         f"{format_money(dec(fee), ccy)} fees (deposit report); the books record "
                         f"{format_money(dec(int(booked.get(month, 0))), ccy)} of fees. The missing fees overstate profit and "
                         f"leave a balance in {self.L.account_name(clearing)} that will never be paid out.", 0.9,
                         _cut(ev), [e] if e else [], affects_cash=False))

    def _bucket(self, desc: str) -> str:
        d = desc.lower()
        if "payout" in d:
            return "payout"
        if "reserve" in d:
            return "reserve"
        if "reimburse" in d:
            return "reimbursement"
        if d.startswith("refund") and "principal" in d:
            return "refunds"
        if "principal" in d:
            return "sales"
        return "fees"

    def _book_bucket(self, entry_id: str, acc: str) -> str:
        g = self._entry_lines[entry_id]
        others = g[g["account_id"] != acc]
        subs = set(others["acc_subtype"])
        types = set(others["acc_type"])
        if subs & set(CASH_SUBTYPES):
            return "payout"
        if "other_income" in subs:
            return "reimbursement"
        memo = str(g["entry_memo"].iloc[0]).lower()
        if "refund" in memo or "return" in memo:
            return "refunds"
        if "revenue" in subs:
            return "sales"
        if "expense" in types:
            return "fees"
        return "other"

    def _settlement_account(self, acc: str, s: pd.DataFrame) -> None:
        ccy = self.acc.loc[acc, "currency"]
        s = s.assign(bucket=[self._bucket(d) for d in s["description"]])
        lines = self.lines[self.lines["account_id"] == acc]
        items = book_items(lines)
        if items.empty:
            return
        items["bucket"] = [self._book_bucket(e, acc) for e in items["entry_id"]]
        # settlement periods from the report rows
        per = []
        for sid, g in s.groupby("external_txn_id"):
            det = g[g["bucket"] != "payout"]
            pay = g[g["bucket"] == "payout"]
            if det.empty:
                continue
            per.append((min(det["txn_date"]), max(det["txn_date"]), sid, g, pay))
        per.sort(key=lambda x: (x[0], str(x[2])))
        # assign book items: non-payout by date in [start, end], payouts by amount/date near the payout
        starts = [p[0] for p in per]
        item_sett = {}
        nonpay = items[items["bucket"] != "payout"]
        for _, it in nonpay.iterrows():
            i = int(np.searchsorted(np.array(starts, dtype="datetime64[D]"), np.datetime64(it["date"]), side="right")) - 1
            if 0 <= i < len(per) and per[i][0] <= it["date"] <= per[i][1]:
                item_sett.setdefault(per[i][2], []).append(it)
        payouts = items[items["bucket"] == "payout"]
        for start, end, sid, g, pay in per:
            if end > self.as_of:
                continue
            bk = pd.DataFrame(item_sett.get(sid, []))
            stmt_tot = g.groupby("bucket")["amount_c"].sum()
            book_tot = bk.groupby("bucket")["amt"].sum() if len(bk) else pd.Series(dtype="int64")
            base_ev = _refs("external_statement_lines", g["id"])
            short = re.sub(r"\s*\(.*?\)", "", str(self.acc.loc[acc, "name"])).replace("Marketplace settlement due - ", "Marketplace ")
            label = f"{short} settlement {str(sid)[-3:]} ({start:%d %b} to {end:%d %b %Y})"
            # payout booked
            pdate = pay["txn_date"].iloc[0] if len(pay) else None
            bpay = payouts[(payouts["date"] >= end) & (payouts["date"] <= (pdate or end) + dt.timedelta(days=7))] \
                if pdate else payouts.iloc[0:0]
            rate = self.rate(ccy, end)
            # (a) fees deducted by the marketplace but missing in the books
            sf, bf = int(stmt_tot.get("fees", 0)), int(-book_tot.get("fees", 0)) if len(book_tot) else 0
            if sf < 0 and bf == 0:
                fee_c = -sf
                ev = base_ev[:]
                if len(bpay):
                    ev += _refs("journal_entries", bpay["entry_id"].head(1))
                fee_acc = self._marketplace_fee_account(acc)
                drafts = []
                for mk, gm in g[g["bucket"] == "fees"].groupby("month"):   # one draft per month the fees fall in
                    c_m = int(-gm["amount_c"].sum())
                    d_m = max(gm["txn_date"])
                    r_m = self.rate(ccy, d_m)
                    e = self.draft(d_m, f"Marketplace fees {label} ({mk})",
                                   "The settlement report shows these fees taken from your sales; the books show the sales as received in full.",
                                   [(fee_acc, int(round(c_m * r_m)), self.base, 1.0), (acc, -c_m, ccy, r_m)], ev)
                    if e:
                        drafts.append(e)
                e = None
                self.add(Raw("fee_not_recorded", "marketplace_fees", acc, end, -fee_c, ccy,
                             f"{format_money(dec(fee_c), ccy)} marketplace fees in {label} not in your books",
                             f"{label} ({start} to {end}) deducted {format_money(dec(fee_c), ccy)} of referral, "
                             f"fulfilment, advertising and other fees before paying out. The books have no fee entry for "
                             f"this period, so profit and the payout are overstated.", 0.9, ev, drafts,
                             affects_cash=False))
                self.raws[-1].payout_entry = bpay["entry_id"].iloc[0] if len(bpay) else None  # type: ignore[attr-defined]
            # (b) reimbursements paid by the marketplace but never booked
            sr = int(stmt_tot.get("reimbursement", 0))
            br = int(book_tot.get("reimbursement", 0)) if len(book_tot) else 0
            if sr - br > 0:
                miss = sr - br
                ids = g[g["bucket"] == "reimbursement"]["id"]
                ev = _refs("external_statement_lines", ids)
                oi = self.acct_by_subtype("other_income")
                drafts = []
                for mk, gm in g[g["bucket"] == "reimbursement"].groupby("month"):
                    c_m = int(gm["amount_c"].sum())
                    d_m = max(gm["txn_date"])
                    r_m = self.rate(ccy, d_m)
                    e = self.draft(d_m, f"Marketplace reimbursements {label} ({mk})",
                                   "The marketplace credited you for lost or damaged units; this income is not in the books.",
                                   [(acc, c_m, ccy, r_m), (oi, -int(round(c_m * r_m)), self.base, 1.0)], ev)
                    if e:
                        drafts.append(e)
                if br != 0:   # partly booked: one draft for the missing part
                    e = self.draft(end, f"Marketplace reimbursements {label}",
                                   "Part of the marketplace reimbursements is not in the books.",
                                   [(acc, miss, ccy, rate), (oi, -int(round(miss * rate)), self.base, 1.0)], ev)
                    drafts = [e] if e else []
                self.add(Raw("unrecorded_receipt", "reimbursement", acc, end, miss, ccy,
                             f"{format_money(dec(miss), ccy)} marketplace reimbursement in {label} not in your books",
                             f"{label} includes {len(ids)} reimbursement line(s) for lost or damaged units worth "
                             f"{format_money(dec(miss), ccy)}; the books never recorded this income.", 0.85, ev,
                             drafts, affects_cash=False))
            # (c) reserve held back: payout smaller than the period's net, money comes later
            res = int(stmt_tot.get("reserve", 0))
            if res < 0 and len(bpay):
                bp = int(-bpay["amt"].iloc[0])
                sp = int(-stmt_tot.get("payout", 0))
                if bp - sp == -res:
                    ev = base_ev + _refs("journal_entries", bpay["entry_id"].head(1))
                    self.add(Raw("timing_difference", "reserve", acc, end, 0, ccy,
                                 f"{format_money(dec(-res), ccy)} held back as a reserve in {label}",
                                 f"The marketplace kept {format_money(dec(-res), ccy)} of {label} as a reserve and paid "
                                 f"{format_money(dec(sp), ccy)}; the books recorded {format_money(dec(bp), ccy)} as received. "
                                 f"The reserve is normally released with a later settlement.", 0.85, ev, affects_cash=False))
                    self.raws[-1].payout_entry = bpay["entry_id"].iloc[0]  # type: ignore[attr-defined]

    def _marketplace_fee_account(self, acc: str) -> str | None:
        lines = self.lines
        ents = lines[lines["account_id"] == acc]["entry_id"]
        other = lines[lines["entry_id"].isin(ents) & (lines["acc_type"] == "expense") &
                      lines["acc_name"].map(is_fee_account_name)]
        if len(other):
            return Counter(other["account_id"]).most_common(1)[0][0]
        return self.acct_by_subtype("opex")

    # ================================================================== 3. cash counts
    def run_cash_counts(self) -> None:
        st = self.stmt[self.stmt["channel"] == "cash_count"]
        for acc, s in st.groupby("account_id"):
            self._cash_count_account(acc, s.sort_values(["txn_date", "_order"]))

    def _cash_count_account(self, acc: str, counts: pd.DataFrame) -> None:
        ccy = self.acc.loc[acc, "currency"] if acc in self.acc.index else self.base
        lines = self.lines[self.lines["account_id"] == acc]
        by_day = lines.groupby("entry_date")["native_c"].sum().sort_index()
        days = np.array(by_day.index, dtype="datetime64[D]")
        cum = np.cumsum(by_day.to_numpy())

        def book_at(d: dt.date) -> int:
            i = int(np.searchsorted(days, np.datetime64(d), side="right")) - 1
            return int(cum[i]) if i >= 0 else 0

        # what other findings already explain on this account (book side errors), by real date
        adj = sorted(self.cash_adjust.get(acc, []))          # (date, cents to add to book to get reality)
        shifts = self.bank_reality_shift.get(acc, [])

        def explained_at(d: dt.date) -> int:
            x = sum(c for (day, c, _) in adj if day <= d)
            for bdate, sdate, amt in shifts:   # book moved on bdate, reality on sdate
                if bdate <= d < sdate:
                    x -= amt
                elif sdate <= d < bdate:
                    x += amt
            return x

        prev_d, prev_gap = None, 0
        for _, c in counts.iterrows():
            d = c["txn_date"]
            counted = int(c["amount_c"])
            gap = counted - (book_at(d) + explained_at(d))    # unexplained difference at this count
            if prev_d is None:
                prev_d, prev_gap = d, gap
                if gap == 0:
                    continue
                delta = gap
                period = f"up to {d}"
            else:
                delta = gap - prev_gap
                period = f"{prev_d} to {d}"
            prev_d, prev_gap = d, gap
            if abs(delta) < 100:
                continue
            ev = _refs("external_statement_lines", [c["id"]])
            rate = self.rate(ccy, d)
            mk = month_key(d)
            if delta < 0:
                exp_acc = self.small_expense_account()
                e = self.draft(d, f"Cash spent but not recorded {mk}",
                               "The physical cash count is lower than the books by this amount after everything else "
                               "we could explain; most often small cash expenses that were never written down.",
                               [(exp_acc, -delta, ccy, rate), (acc, delta, ccy, rate)], ev)
                self.add(Raw("cash_count_shortfall", "cash_shortfall", acc, d, delta, ccy,
                             f"Cash count {format_money(dec(-delta), ccy)} short in {mk}",
                             f"Between {period}, the counted cash in {self.L.account_name(acc)} fell "
                             f"{format_money(dec(-delta), ccy)} more than the books say. This usually means small "
                             f"cash payments (porters, tea, transport, samples) were made but not recorded.",
                             0.8, ev, [e] if e else []))
            else:
                e = None   # no automatic draft: the owner must say what the extra cash is (a missed sale, a double entry ...)
                self.add(Raw("other", "cash_surplus", acc, d, delta, ccy,
                             f"Cash count {format_money(dec(delta), ccy)} over in {mk}",
                             f"Between {period}, the counted cash in {self.L.account_name(acc)} is "
                             f"{format_money(dec(delta), ccy)} higher than the books explain: a cash receipt may be "
                             f"missing, or a cash payment was entered twice.", 0.5, ev, [e] if e else []))

    # ================================================================== 4. book-only checks
    def run_book_checks(self) -> None:
        self._duplicate_bills()
        self._duplicate_payables()
        self._advance_paid_twice()
        self._supplier_credit_as_income()
        self._pos_revenue()
        self._unusual_accounts()
        self._fx_rates_off_market()
        self._receivables()

    def _duplicate_bills(self) -> None:
        """Same supplier, same amount, entered within a few days under a different number, and only
        one of the two was ever paid."""
        b = self.L.bills
        if b.empty:
            return
        b = b[b["issue_date"] <= self.as_of].copy()
        b["tot_c"] = (pd.to_numeric(b["total"]) * 100).round().astype("int64")
        b["paid_c"] = (pd.to_numeric(b["paid"]) * 100).round().astype("int64")
        for (cp, tot), g in b.groupby(["counterparty_id", "tot_c"]):
            if len(g) < 2:
                continue
            g = g.sort_values(["issue_date", "id"])
            rows = list(g.itertuples())
            used = set()
            for i, x in enumerate(rows):
                for y in rows[i + 1:]:
                    if y.id in used or x.id in used or (y.issue_date - x.issue_date).days > 7:
                        continue
                    if (x.paid_c > 0) == (y.paid_c > 0):
                        continue           # both paid (two real bills) or both open: not enough evidence
                    dup = y if y.paid_c == 0 else x
                    orig = x if dup is y else y
                    used.add(dup.id)
                    ccy = dup.currency
                    je = dup.journal_entry_id if isinstance(dup.journal_entry_id, str) else None
                    ev = _refs("bills", [dup.id, orig.id]) + (_refs("journal_entries", [je]) if je else [])
                    e = self._reverse_entry(je, f"Reverse duplicate bill {dup.bill_no}",
                                            f"Same supplier and amount as {orig.bill_no}, a few days apart; only one was paid.",
                                            ev) if je else None
                    self.add(Raw("duplicate_entry", "duplicate_bill", None, dup.issue_date, 0, ccy,
                                 f"Bill {dup.bill_no} from {dup.supplier_name} looks like a copy of {orig.bill_no}",
                                 f"{dup.supplier_name}: bills {orig.bill_no} ({orig.issue_date}) and {dup.bill_no} "
                                 f"({dup.issue_date}) are both for {format_money(dec(tot), ccy)}; one was paid, the other "
                                 f"never. Costs and supplier payables are overstated by one bill.", 0.75, ev,
                                 [e] if e else [], affects_cash=False))

    def _duplicate_payables(self) -> None:
        """Two supplier invoices for the same amount within 10 days that name the same document
        number or the same supplier - typically a bill entered from the PDF and again from the system."""
        l = self.lines
        ap = l[(l["acc_subtype"] == "ap") & (l["native_c"] < 0)]
        if ap.empty:
            return
        flagged = {e.split(":", 1)[1] for r in self.raws if r.category == "duplicate_bill" for e in r.evidence if e.startswith("journal_entries:")}
        hdr = ap.groupby("entry_id").agg(date=("entry_date", "first"), amt=("native_c", "sum"),
                                         ref=("reference", "first"), memo=("entry_memo", "first"),
                                         acc=("account_id", "first"), ccy=("currency", "first"))
        hdr = hdr.reset_index().sort_values(["date", "entry_id"])
        for amt, g in hdr.groupby("amt"):
            if len(g) < 2:
                continue
            rows = list(g.itertuples())
            for i, x in enumerate(rows):
                for y in rows[i + 1:]:
                    if (y.date - x.date).days > 10:
                        break
                    dn_x = set(re.findall(r"[A-Za-z]*\d{3,}", str(x.ref) + " " + str(x.memo)))
                    dn_y = set(re.findall(r"[A-Za-z]*\d{3,}", str(y.ref) + " " + str(y.memo)))
                    same_doc = bool(dn_x & dn_y)
                    tx, ty = _tokens(x.memo), _tokens(y.memo)
                    same_sup = bool(tx and ty and len(tx & ty) / max(1, min(len(tx), len(ty))) >= 0.6)
                    if not (same_doc or (same_sup and x.ref != y.ref and (y.date - x.date).days <= 3)):
                        continue
                    dup = y
                    if dup.entry_id in flagged:
                        continue
                    flagged.add(dup.entry_id)
                    ev = _refs("journal_entries", [dup.entry_id, x.entry_id])
                    e = self._reverse_entry(dup.entry_id, f"Reverse duplicate supplier bill {dup.ref}",
                                            f"Same amount and document as {x.ref} entered {(y.date - x.date).days} day(s) earlier.", ev)
                    self.add(Raw("duplicate_entry", "duplicate_bill", None, dup.date, 0, dup.ccy,
                                 f"Supplier bill {dup.ref} looks like a copy of {x.ref}",
                                 f"'{dup.memo}' ({format_money(dec(-amt), dup.ccy)}, {dup.date}) repeats '{x.memo}' "
                                 f"({x.date}): same amount{' and same document number' if same_doc else ''}. Costs and "
                                 f"supplier payables are overstated by one bill.", 0.8, ev, [e] if e else [],
                                 affects_cash=False))
                    break

    def _supplier_credit_as_income(self) -> None:
        """A supplier credit note reduces what the goods cost; booked as income it inflates revenue lines."""
        l = self.lines
        ent = l.groupby("entry_id").agg(subs=("acc_subtype", lambda x: tuple(sorted(set(x)))))
        cand = ent[ent["subs"].apply(lambda t: set(t) == {"ap", "other_income"})].index
        if not len(cand):
            return
        cogs = self.acct_by_subtype("cogs")
        for eid in cand:
            g = self._entry_lines[eid]
            if not (g[g["acc_subtype"] == "ap"]["native_c"] > 0).all():
                continue
            inc = g[g["acc_subtype"] == "other_income"]
            amt = int(-inc["base_c"].sum())
            ev = _refs("journal_entries", [eid])
            e = self.draft(g["entry_date"].iloc[0], f"Reclassify supplier credit '{g['entry_memo'].iloc[0]}'",
                           "A credit from a supplier lowers the cost of the goods; it is not income.",
                           [(inc["account_id"].iloc[0], amt, self.base, 1.0), (cogs, -amt, self.base, 1.0)], ev)
            self.add(Raw("other", "misclassified", None, g["entry_date"].iloc[0], 0, self.base,
                         f"Supplier credit booked as income: '{g['entry_memo'].iloc[0]}'",
                         f"{format_money(dec(amt), self.base)} credit from a supplier on {g['entry_date'].iloc[0]} was "
                         f"booked as other income. It should reduce cost of sales; gross margin is understated and "
                         f"other income overstated.", 0.75, ev, [e] if e else [], affects_cash=False))

    def _pos_revenue(self) -> None:
        """Daily revenue in the books vs. net sales in the POS. Extra revenue in the books on a POS day is
        usually something that is not a sale (gift cards sold, deposits); grouped by month."""
        try:
            pos = self.L.db.query_df(
                "SELECT id, sales_date, net_sales, basis FROM pos_sales_daily WHERE company_id = ?", [self.L.company_id])
        except Exception:  # noqa: BLE001 - view missing in very old databases
            return
        if pos.empty:
            return
        pos["d"] = pd.to_datetime(pos["sales_date"]).dt.date
        pos = pos[pos["d"] <= self.as_of]
        pos_day = pos.groupby("d").agg(net=("net_sales", lambda x: int(round(pd.to_numeric(x).sum() * 100))),
                                       ids=("id", list))
        l = self.lines
        ar_entries = set(l[l["acc_subtype"] == "ar"]["entry_id"])   # invoice (trade) sales are not till sales
        rev = l[(l["acc_subtype"] == "revenue") & l["entry_date"].isin(pos_day.index) & ~l["entry_id"].isin(ar_entries)]
        book_day = (-rev.groupby("entry_date")["base_c"].sum())
        j = pos_day.join(book_day.rename("book"), how="inner")
        j["diff"] = j["book"] - j["net"]
        j = j[j["diff"].abs() >= 100]
        if j.empty:
            return
        liab = self.acct_by_subtype("other_liability", name_re=r"gift|voucher|deposit")
        rev_acc = self.acct_by_subtype("revenue")
        j["month"] = [f"{d.year:04d}-{d.month:02d}" for d in j.index]
        for month, g in j.groupby("month"):
            over = int(g["diff"].sum())
            if abs(over) < 100:
                continue
            days = list(g.index)
            ev = [make_ref("pos_sales_daily", i) for ids in g["ids"] for i in ids][:50]
            ev += _refs("journal_entries", rev[rev["entry_date"].isin(days)]["entry_id"].unique()[:100])
            last = max(days)
            target = liab if (liab and over > 0) else None
            legs = [(rev_acc, over, self.base, 1.0), (target, -over, self.base, 1.0)] if target else []
            e = self.draft(last, f"Move non-sales receipts out of revenue {month}",
                           "Revenue in the books is higher than net sales in the till system on these days; the extra "
                           "is most often gift cards sold (a debt to the customer until redeemed).", legs, ev) if legs else None
            self.add(Raw("other", "pos_revenue_gap", None, last, 0, self.base,
                         f"Revenue in the books {'above' if over > 0 else 'below'} till sales by "
                         f"{format_money(dec(abs(over)), self.base)} in {month}",
                         f"On {len(days)} day(s) in {month}, the books record {format_money(dec(abs(over)), self.base)} "
                         f"{'more' if over > 0 else 'less'} revenue than the POS shows as net sales."
                         + (f" A gift-card liability account exists ({self.L.account_name(liab)}): gift cards sold are a "
                            f"debt until redeemed, not revenue." if liab and over > 0 else ""),
                         0.7, ev, [e] if e else [], affects_cash=False))

    def _advance_paid_twice(self) -> None:
        l = self.lines
        inv = l[(l["acc_subtype"] == "inventory") & (l["native_c"] > 0)]
        if inv.empty:
            return
        ent = l[l["entry_id"].isin(inv["entry_id"])]
        credit_kind = ent[ent["native_c"] < 0].groupby("entry_id")["acc_subtype"].agg(lambda s: set(s))
        via_adv = [e for e, k in credit_kind.items() if k == {"prepaid"}]
        via_cash = {e for e, k in credit_kind.items() if k and k <= set(CASH_SUBTYPES)}
        amt = inv.groupby("entry_id")["native_c"].sum()
        date = inv.groupby("entry_id")["entry_date"].first()
        cash_by_amt = defaultdict(list)
        for e in via_cash:
            cash_by_amt[int(amt[e])].append(e)
        used = set()
        for e in sorted(via_adv):
            for c in cash_by_amt.get(int(amt[e]), []):
                if c in used or abs((date[c] - date[e]).days) > 3:
                    continue
                used.add(c)
                cl = l[l["entry_id"] == c]
                cash_acc = cl[cl["native_c"] < 0]["account_id"].iloc[0]
                ccy = self.acc.loc[cash_acc, "currency"]
                ev = _refs("journal_entries", [c, e])
                d = self._reverse_entry(c, "Reverse cash purchase already paid by advance",
                                        "The goods were paid for with the advance; the second (cash) payment did not happen.", ev)
                self.add(Raw("duplicate_entry", "advance_twice", cash_acc, date[c], int(amt[c]), ccy,
                             f"Goods paid twice: advance and cash ({format_money(dec(amt[c]), ccy)})",
                             f"On {date[c]} the books record goods worth {format_money(dec(amt[c]), ccy)} received against "
                             f"an advance already paid, and the same goods again as a cash purchase. Only one payment "
                             f"really happened, so the books show too little cash.", 0.85, ev, [d] if d else []))
                self.cash_adjust[cash_acc].append((date[c], int(amt[c]), f"dup:{c}"))
                break

    def _unusual_accounts(self) -> None:
        """An entry whose description is normally booked to account X but this time went to Y."""
        l = self.lines
        pl = l[l["acc_type"].isin(["expense", "income"])]
        if pl.empty:
            return
        per_entry = pl.groupby("entry_id")["account_id"].nunique()
        single = pl[pl["entry_id"].isin(per_entry[per_entry == 1].index)]
        single = single.drop_duplicates("entry_id")
        single = single.assign(stem=[_stem(m) for m in single["entry_memo"].fillna("")])
        for stem, g in single.groupby("stem"):
            if len(stem) < 6 or len(g) < 5:
                continue
            cnt = Counter(g["account_id"])
            top, n_top = cnt.most_common(1)[0]
            if n_top / len(g) < 0.6 or n_top == len(g):
                continue
            for r in g[g["account_id"] != top].itertuples():
                if self.acc.loc[r.account_id, "type"] != self.acc.loc[top, "type"] and \
                        {self.acc.loc[r.account_id, "type"], self.acc.loc[top, "type"]} != {"income", "expense"}:
                    continue
                amt = int(r.base_c)
                ev = _refs("journal_entries", [r.entry_id])
                e = self.draft(r.entry_date, f"Reclassify '{r.entry_memo}'",
                               f"Entries described like this are normally booked to {self.L.account_name(top)}.",
                               [(top, amt, self.base, 1.0), (r.account_id, -amt, self.base, 1.0)], ev)
                self.add(Raw("other", "misclassified", None, r.entry_date, 0, self.base,
                             f"'{r.entry_memo}' booked to {self.L.account_name(r.account_id)}",
                             f"{n_top} of {len(g)} entries described like '{r.entry_memo}' go to "
                             f"{self.L.account_name(top)}, but this one ({format_money(dec(abs(amt)), self.base)} on "
                             f"{r.entry_date}) went to {self.L.account_name(r.account_id)}. Cash is unaffected; the "
                             f"profit and loss lines are.", 0.7, ev, [e] if e else [], affects_cash=False))

    def _fx_rates_off_market(self) -> None:
        l = self.lines
        fl = l[(l["currency"] != self.base) & (l["acc_type"].isin(["expense", "income"]))]
        if fl.empty:
            return
        rows = []
        for r in fl.itertuples():
            ser = self.L.fx_series(r.currency)
            mr = self.L.fx_rate(r.currency, r.entry_date)
            if not mr or ser is None:
                continue
            win = ser[(ser.index >= pd.Timestamp(r.entry_date - dt.timedelta(days=45))) &
                      (ser.index <= pd.Timestamp(r.entry_date))]
            if win.empty:
                continue
            lo, hi = float(win.min()), float(win.max())
            rate = float(r.fx_rate)
            # an average or same-day rate lies inside the recent range; a stale one falls outside it
            if rate < lo * (1 - FX_RATE_TOLERANCE / 2) or rate > hi * (1 + FX_RATE_TOLERANCE / 2):
                rows.append((r, mr, abs(rate / mr - 1)))
        # frozen rate: the very same rate reused on dates weeks apart while the market moved
        fl2 = fl.assign(rk=fl["fx_rate"].round(6))
        frozen = set()
        for (ccy, rk), g in fl2.groupby(["currency", "rk"]):
            ds = sorted(set(g["entry_date"]))
            if len(ds) >= 2 and (ds[-1] - ds[0]).days >= 10:
                mk = [self.L.fx_rate(ccy, d) for d in ds]
                mk = [x for x in mk if x]
                if mk and (max(mk) / min(mk) - 1) > 0.005:
                    frozen.update(g["line_id"])
        rows = [x for x in rows if x[0].line_id in frozen]
        by_month = defaultdict(list)
        for r, mr, dev in rows:
            by_month[(r.month, r.currency)].append((r, mr, dev))
        for (month, ccy), xs in sorted(by_month.items()):
            ev = _refs("journal_entries", [x[0].entry_id for x in xs])
            err = sum(int(round(x[0].native_c * x[1])) - int(x[0].base_c) for x in xs)
            entries = []
            for r, mr, dev in xs:
                delta = int(round(r.native_c * mr)) - int(r.base_c)
                fx_acc = self.acct_by_subtype("fx_loss")
                e = self.draft(r.entry_date, f"Restate '{r.entry_memo}' at the market rate",
                               f"Booked at {r.fx_rate:.4f}; the market rate that day was {mr:.4f}.",
                               [(r.account_id, delta, self.base, 1.0), (fx_acc, -delta, self.base, 1.0)],
                               _refs("journal_entries", [r.entry_id]))
                if e:
                    entries.append(e)
            self.add(Raw("fx_difference", "fx_rate_off_market", None, max(x[0].entry_date for x in xs), 0, self.base,
                         f"{len(xs)} {ccy} item(s) in {month} booked at an out-of-date exchange rate",
                         f"In {month}, {len(xs)} {ccy} income/expense line(s) were converted at a rate more than "
                         f"{FX_RATE_TOLERANCE:.2%} away from that day's market rate (e.g. {xs[0][0].fx_rate:.4f} vs "
                         f"{xs[0][1]:.4f}). The base-currency cost is mis-stated by "
                         f"{format_money(dec(abs(err)), self.base)}; cash is unaffected.", 0.75, ev, entries,
                         affects_cash=False))

    def _receivables(self) -> None:
        inv = self.L.invoices
        if inv.empty:
            return
        inv = inv[inv["issue_date"] <= self.as_of].copy()
        pays = self.L.invoice_payments
        pays = pays[pays["payment_date"] <= self.as_of]
        paid = pays.groupby("invoice_id")["amount"].sum()
        last_pay_inv = pays.groupby("invoice_id")["payment_date"].max()
        # credit from receipts we found on statements but the books missed
        found_credit = Counter()
        for r in self.raws:
            if getattr(r, "ar_credit", None) and r.ar_credit[1]:
                found_credit[r.ar_credit[1]] += r.impact_c
        inv["paid_asof"] = inv["id"].map(paid).fillna(0).astype(float)
        inv["out_c"] = ((pd.to_numeric(inv["total"]) - inv["paid_asof"]) * 100).round().astype("int64")
        inv["last_pay"] = inv["id"].map(last_pay_inv)
        open_inv = inv[inv["out_c"] > 0]
        if open_inv.empty:
            return
        ar_map = self._invoice_ar_accounts()
        bad = self.acct_by_subtype("other_expense", name_re=r"bad") or self.acct_by_subtype("other_expense")
        for cust, g in open_inv.groupby("customer_name"):
            all_c = inv[inv["customer_name"] == cust]
            last_payment = all_c["last_pay"].dropna().max() if all_c["last_pay"].notna().any() else None
            last_invoice = all_c["issue_date"].max()
            out_c = int(g["out_c"].sum()) - int(found_credit.get(cust, 0))
            if out_c <= 0:
                continue
            ccy = g["currency"].iloc[0]
            ref_date = max([d for d in (last_payment, last_invoice) if d is not None])
            idle = (self.as_of - ref_date).days
            overdue = g[g["due_date"].notna() & (g["due_date"] < self.as_of - dt.timedelta(days=STALE_DAYS))]
            ev = _refs("invoices", g["id"].head(MAX_EVIDENCE))
            ar_acc = ar_map.get(g["journal_entry_id"].dropna().iloc[0]) if g["journal_entry_id"].notna().any() else None
            ar_acc = ar_acc or self._default_ar(ccy)
            rate = float(pd.to_numeric(g["fx_rate"]).iloc[0]) if ccy != self.base else 1.0
            if idle >= PHANTOM_DAYS:
                wo_date = ref_date + dt.timedelta(days=PHANTOM_DAYS)
                e = self.draft(min(wo_date, self.as_of), f"Write off {cust}",
                               f"No payment for {idle} days; the debt is very unlikely to be collected.",
                               [(bad, int(round(out_c * rate)), self.base, 1.0), (ar_acc, -out_c, ccy, rate)], ev)
                self.add(Raw("stale_receivable", "phantom_receivable", ar_acc, min(wo_date, self.as_of), 0, ccy,
                             f"{cust} owes {format_money(dec(out_c), ccy)} and has paid nothing for {idle} days",
                             f"Your books still count {format_money(dec(out_c), ccy)} from {cust} as money you will get, "
                             f"but the last payment or sale was on {ref_date}. After {PHANTOM_DAYS}+ days this is "
                             f"unlikely to be collected; consider writing it off (draft below) or chasing it.",
                             0.8, ev, [e] if e else [], affects_cash=False))
            elif len(overdue):
                od = int(overdue["out_c"].sum())
                worst = (self.as_of - overdue["due_date"].min()).days
                self.add(Raw("stale_receivable", "overdue_receivable", ar_acc, self.as_of, 0, ccy,
                             f"{cust}: {format_money(dec(od), ccy)} more than {STALE_DAYS} days overdue",
                             f"{len(overdue)} invoice(s) from {cust} are past due, the oldest by {worst} days. "
                             f"Chase the payment before it becomes a loss.", 0.7, _refs("invoices", overdue["id"]),
                             affects_cash=False))

    # ================================================================== assemble
    def finalize_amount_differences(self) -> None:
        """Turn raw near-pair differences into grouped findings, merging payout shortfalls that a
        settlement report already explains (fees / reserve)."""
        payout_explained = {}
        for r in self.raws:
            pe = getattr(r, "payout_entry", None)
            if pe:
                payout_explained[pe] = r
        keep, groups = [], defaultdict(list)
        for r in self.raws:
            if not hasattr(r, "pnl_hint"):
                keep.append(r)
                continue
            entry, others = r.pnl_hint
            if entry in payout_explained:
                tgt = payout_explained[entry]
                tgt.evidence = _cut(tgt.evidence + r.evidence)
                tgt.affects_cash = True
                tgt.account_id_cash = r.account_id  # type: ignore[attr-defined]
                tgt.cash_impact = r.impact_c       # type: ignore[attr-defined]
                tgt.cash_ccy = r.currency          # type: ignore[attr-defined]
                continue
            groups[(r.category, r.account_id, month_key(r.date))].append(r)
        for (cat, acc, month), rs in sorted(groups.items()):
            ccy = rs[0].currency
            tot = sum(x.impact_c for x in rs)
            ev = _cut([e for x in rs for e in x.evidence])
            n = len(rs)
            rate = self.rate(ccy, max(x.date for x in rs))
            if cat == "fx_conversion":
                fx_acc = self.acct_by_subtype("fx_loss")
                e = self.draft(max(x.date for x in rs), f"Currency conversions {month}: actual amount received",
                               "The bank credited a different amount than the books recorded for these conversions.",
                               [(acc, tot, ccy, rate), (fx_acc, -int(round(tot * rate)), self.base, 1.0)], ev)
                keep.append(Raw("fx_difference", cat, acc, max(x.date for x in rs), tot, ccy,
                                f"{n} currency conversion(s) in {month} credited {format_money(dec(abs(tot)), ccy)} "
                                f"{'less' if tot < 0 else 'more'} than booked",
                                f"In {month}, {n} conversion(s) into {self.L.account_name(acc)} were booked at a rate "
                                f"(e.g. a monthly average) instead of the amount the bank actually credited. The books "
                                f"show {format_money(dec(abs(tot)), ccy)} {'more' if tot < 0 else 'less'} cash than "
                                f"really arrived.", 0.85, ev, [e] if e else []))
            elif cat == "loan_interest":
                int_acc = self.acct_by_subtype("interest") or self.acct_by_subtype("other_expense")
                e = self.draft(max(x.date for x in rs), f"Loan interest {month}",
                               "The bank took more than the booked loan repayment; the difference is normally interest.",
                               [(int_acc, int(round(-tot * rate)), self.base, 1.0), (acc, tot, ccy, rate)], ev)
                keep.append(Raw("unrecorded_expense", cat, acc, max(x.date for x in rs), tot, ccy,
                                f"Loan repayment in {month} {format_money(dec(-tot), ccy)} higher than booked",
                                f"In {month}, the bank collected {format_money(dec(-tot), ccy)} more for the loan than the "
                                f"books recorded (the books show only the principal). The difference is most likely "
                                f"interest, an expense that is missing from the books.", 0.8, ev, [e] if e else []))
            elif cat == "payout_short":
                fee_acc = self._marketplace_fee_account(self._clearing_of(rs[0]) or acc)
                e = self.draft(max(x.date for x in rs), f"Payout shortfall {month}",
                               "The payout that reached the account was smaller than the amount booked; most often "
                               "fees or a reserve were deducted.",
                               [(fee_acc, int(round(-tot * rate)), self.base, 1.0), (acc, tot, ccy, rate)], ev)
                keep.append(Raw("fee_not_recorded", cat, acc, max(x.date for x in rs), tot, ccy,
                                f"{n} payout(s) in {month} smaller than booked by {format_money(dec(-tot), ccy)}",
                                f"In {month}, {n} payout(s) reached {self.L.account_name(acc)} with "
                                f"{format_money(dec(-tot), ccy)} less than the books recorded. Deductions (fees, reserve) "
                                f"are missing from the books.", 0.75, ev, [e] if e else []))
            else:
                keep.append(Raw(rs[0].kind, cat, acc, max(x.date for x in rs), tot, ccy,
                                f"{n} amount difference(s) in {month} on {self.L.account_name(acc)}",
                                f"In {month}, {n} item(s) on {self.L.account_name(acc)} match the statement by date "
                                f"and direction but not by amount; together the statement shows "
                                f"{format_money(dec(tot), ccy)} versus the books.", 0.6, ev))
        self.raws = keep

    def _clearing_of(self, raw: Raw) -> str | None:
        entry, others = getattr(raw, "pnl_hint", (None, []))
        for o in others:
            if o in self.acc.index and self.acc.loc[o, "subtype"] == "ar":
                return o
        return None

    def run(self) -> None:
        self.run_statement_accounts()
        self.run_settlements()
        self.finalize_amount_differences()
        self.run_book_checks()
        self.run_cash_counts()


# ---------------------------------------------------------------------------- public API
def cash_truth(L: Ledger, as_of: dt.date) -> CashTruthResult:
    return run_cash_truth(L, as_of)[0]


def run_cash_truth(L: Ledger, as_of: dt.date) -> tuple[CashTruthResult, list[Raw]]:
    """Result + the raw findings (raw findings keep the per-account detail used by P&L/forecast)."""
    run = CashTruthRun(L, as_of)
    run.run()
    base = L.base
    acc = L.accounts

    # ---- channels
    channels: list[ChannelBalance] = []
    book_total = ver_total = 0
    book_ev, ver_ev = [], []
    stmt = run.stmt
    lines = run.lines
    def _primary(chs):
        c = Counter(chs)
        for pref in ("bank", "wallet", "cash_count"):
            if pref in c:
                return pref
        return c.most_common(1)[0][0]
    stmt_accounts = stmt.groupby("account_id")["channel"].agg(_primary)
    cash_ids = [a for a in run.cash_accounts.index] + [a for a in stmt_accounts.index if a not in run.cash_accounts.index]
    for a in cash_ids:
        if a not in acc.index:
            continue
        ccy = acc.loc[a, "currency"]
        bl = lines[lines["account_id"] == a]
        book_c = int(bl["native_c"].sum())
        ch = stmt_accounts.get(a)
        sa = stmt[(stmt["account_id"] == a) & (stmt["channel"] == ch)]
        if ch is None:
            continue
        rate = run.rate(ccy, as_of)
        if ch == "cash_count":
            last = sa.sort_values(["txn_date", "_order"]).iloc[-1]
            after = bl[bl["entry_date"] > last["txn_date"]]["native_c"].sum()
            ver_c = int(last["amount_c"]) + int(after)
            ev = _refs("external_statement_lines", [last["id"]])
        else:
            ver_c = _statement_balance(sa)
            ev = _refs("external_statement_lines", sa.tail(5)["id"])
        bref = _refs("journal_lines", bl.tail(5)["line_id"])
        channels.append(ChannelBalance(channel=ch, account_id=a, label=L.account_name(a),
                                       book_balance=dec(book_c), verified_balance=dec(ver_c),
                                       difference=dec(ver_c - book_c), currency=ccy, evidence=ev + bref))
        if acc.loc[a, "subtype"] in CASH_SUBTYPES:
            book_total += int(round(book_c * rate))
            ver_total += int(round(ver_c * rate))
            book_ev += bref
            ver_ev += ev

    # ---- findings
    findings: list[Finding] = []
    all_entries: list[DraftJournalEntry] = []
    explained = 0
    order = {"unrecorded_expense": 0, "cash_count_shortfall": 1, "fee_not_recorded": 2, "unrecorded_receipt": 3,
             "duplicate_entry": 4, "fx_difference": 5, "stale_receivable": 6, "other": 7, "timing_difference": 8}
    raws = sorted(run.raws, key=lambda r: (order.get(r.kind, 9), r.date, r.category, r.title))
    for i, r in enumerate(raws, start=1):
        cash_acc = getattr(r, "account_id_cash", r.account_id)
        cash_imp = getattr(r, "cash_impact", r.impact_c)
        cash_ccy = getattr(r, "cash_ccy", r.currency)
        rate = run.rate(cash_ccy, as_of)
        base_c = int(round(cash_imp * rate))
        if r.affects_cash and cash_acc in run.cash_accounts.index:
            explained += base_c
            extra = getattr(r, "extra_cash", None)
            if extra:  # misrouted: the other cash account is overstated by the same amount
                explained += int(round(extra[1] * run.rate(acc.loc[extra[0], "currency"], as_of)))
        amount = base_c if r.affects_cash else int(round(r.impact_c * run.rate(r.currency, as_of)))
        if r.kind == "stale_receivable" or r.category in ("misclassified", "fx_rate_off_market", "misrouted_receipt"):
            amount = 0
        f = Finding(id=f"f-{i:04d}", kind=r.kind, title=r.title[:200], explanation=r.explanation[:1500],
                    amount=dec(amount), currency=base, confidence=round(r.confidence, 2),
                    evidence=_cut(r.evidence)[:MAX_EVIDENCE], proposed_entries=r.entries)
        findings.append(f)
        all_entries.extend(r.entries)
    run.findings_raw = raws

    gap = ver_total - book_total
    cur = base
    summary = _summary(L, as_of, book_total, ver_total, gap, explained, findings)
    res = CashTruthResult(
        company_id=L.company_id, as_of=as_of, currency=base,
        book_cash=Figure(key="book_cash", label="Cash according to your books", value=dec(book_total), currency=cur,
                         evidence=_cut(book_ev)[:MAX_EVIDENCE], evidence_count=int(lines["acc_subtype"].isin(CASH_SUBTYPES).sum()),
                         formula="Sum of posted journal lines on cash, bank and wallet accounts up to the as-of date, "
                                 "foreign balances at the as-of exchange rate.",
                         hint="What the bookkeeping says you have in cash, bank and wallets."),
        verified_cash=Figure(key="verified_cash", label="Cash you can prove", value=dec(ver_total), currency=cur,
                             evidence=_cut(ver_ev)[:MAX_EVIDENCE], evidence_count=int(len(stmt)),
                             formula="Latest statement balance per bank/wallet account and latest physical cash count "
                                     "(plus booked movements after the count) up to the as-of date.",
                             hint="Balances confirmed by a third party (bank, wallet) or a physical count."),
        gap=Figure(key="gap", label="Cash Truth gap (provable minus books)", value=dec(gap), currency=cur,
                   formula="verified cash - book cash", hint="Negative: the books show more cash than really exists."),
        explained=Figure(key="explained", label="Part of the gap explained by findings", value=dec(explained),
                         currency=cur, formula="Sum of the cash effect of all findings that are still open at the as-of date."),
        unexplained=Figure(key="unexplained", label="Part of the gap not yet explained", value=dec(gap - explained),
                           currency=cur, formula="gap - explained"),
        channels=channels, findings=findings, proposed_entries=all_entries, summary=summary)
    return res, raws


def _statement_balance(sa: pd.DataFrame) -> int:
    if sa.empty:
        return 0
    s = sa.sort_values(["txn_date", "source_file", "_order"])
    last = s.iloc[-1]
    if pd.notna(last["balance"]):
        # balance after the last line of the last day
        return int(round(float(last["balance"]) * 100))
    return int(s["amount_c"].sum())


def _summary(L: Ledger, as_of, book, ver, gap, explained, findings: list[Finding]) -> str:
    b = L.base
    k = Counter(f.kind for f in findings)
    top = sorted([f for f in findings if f.amount != 0], key=lambda f: -abs(f.amount))[:3]
    parts = [f"As of {as_of}, your books show {format_money(dec(book), b)} in cash, bank and wallets; "
             f"statements and cash counts confirm {format_money(dec(ver), b)}."]
    if gap == 0:
        parts.append("The two agree.")
    else:
        parts.append(f"The books show {format_money(dec(abs(gap)), b)} {'more' if gap < 0 else 'less'} than you can prove. "
                     f"{len(findings)} finding(s) explain {format_money(dec(explained), b)} of the "
                     f"{format_money(dec(gap), b)} gap; {format_money(dec(gap - explained), b)} is still unexplained.")
    if k:
        parts.append("Findings: " + ", ".join(f"{n} {kind.replace('_', ' ')}" for kind, n in k.most_common()) + ".")
    if top:
        parts.append("Largest: " + "; ".join(f"{f.title}" for f in top) + ".")
    parts.append("Proposed corrections are drafts only and need your approval.")
    return " ".join(parts)
