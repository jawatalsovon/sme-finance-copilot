"""Shared machinery for the SME data generators (D1).

Money is ALWAYS integer minor units (cents / poisha / fen / pence): no float rounding drift,
so "truth P&L reproduces to the unit" can be checked exactly.

Model
  World   - one company's TRUTH: accounts, balances, every journal entry that really happened.
  Book    - a copy of the truth entries that the owner's books actually contain; planted problems
            edit it (drop / alter / add entries). `Problems` records every edit with its exact fix.
  The answer key is derived from (truth, book, problems) - never typed by hand.
"""
from __future__ import annotations

import copy
import datetime as dt
import gzip
import hashlib
import io
import json
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
ANCHORS = ROOT / "data" / "anchors"
SYN = ROOT / "data" / "synthetic"
D0, D1 = dt.date(2020, 1, 1), dt.date(2026, 9, 30)
GEN_VERSION = "1.0"


# ----------------------------------------------------------------------------- basics
def days(a: dt.date = D0, b: dt.date = D1):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def month_end(d: dt.date) -> dt.date:
    return (dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)) - dt.timedelta(days=1)


def month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    last = month_end(dt.date(y, m, 1)).day
    return dt.date(y, m, min(d.day, last))


def ym(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def rng_for(seed: int, *keys) -> np.random.Generator:
    h = hashlib.sha256(("|".join(str(k) for k in (seed,) + keys)).encode()).digest()
    return np.random.default_rng(int.from_bytes(h[:8], "little"))


def money(c: int | None) -> str:
    """Integer minor units -> '1234.50' (empty for None)."""
    if c is None:
        return ""
    s = "-" if c < 0 else ""
    c = abs(int(c))
    return f"{s}{c // 100}.{c % 100:02d}"


def rnd(x: float) -> int:
    """Round half away from zero to an int (deterministic)."""
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def weighted_split(total: int, weights: list[float]) -> list[int]:
    """Split an int total across weights so the parts sum exactly to total."""
    w = np.asarray(weights, dtype=float)
    w = w / w.sum()
    parts = [int(total * x) for x in w]
    rem = total - sum(parts)
    order = np.argsort(-(total * w - np.floor(total * w)))
    for i in range(abs(rem)):
        parts[int(order[i % len(parts)])] += 1 if rem > 0 else -1
    return parts


def scale_to_targets(raw: dict[dt.date, float], targets: dict[int, int]) -> dict[dt.date, int]:
    """Scale a daily raw series so each calendar year sums EXACTLY to targets[year] (minor units)."""
    out: dict[dt.date, int] = {}
    by_year: dict[int, list[dt.date]] = defaultdict(list)
    for d in raw:
        by_year[d.year].append(d)
    for y, ds in by_year.items():
        tot = sum(raw[d] for d in ds)
        f = targets[y] / tot if tot > 0 else 0
        vals = [raw[d] * f for d in ds]
        ints = [int(v) for v in vals]
        rem = targets[y] - sum(ints)
        order = sorted(range(len(ds)), key=lambda i: -(vals[i] - ints[i]))
        for i in range(rem):
            ints[order[i % len(ds)]] += 1
        out.update(dict(zip(ds, ints)))
    return out


# ----------------------------------------------------------------------------- anchors
class Anchors:
    """Public series from data/anchors/ with gap-filling for dates beyond each publisher's last value."""

    def __init__(self) -> None:
        r = lambda f: pd.read_csv(ANCHORS / f)  # noqa: E731
        fx = r("fx_h10_daily.csv")
        self.fx = {dt.date.fromisoformat(a): (b, c, d) for a, b, c, d in fx.itertuples(index=False)}
        self.bdt = dict(r("bdt_usd_monthly_modelled.csv")[["month", "bdt_per_usd"]].itertuples(index=False))
        self.gscpi_m = dict(r("gscpi_monthly.csv").itertuples(index=False))
        self.pink = r("pinksheet_monthly.csv").set_index("month")
        self.wfp = r("wfp_bgd_retail_monthly.csv")
        self.cpi = dict(r("ons_cpi_d7bt_monthly.csv").itertuples(index=False))
        self.rsi = dict(r("ons_retail_nonfood_volume_monthly.csv").itertuples(index=False))
        br = r("boe_bank_rate_changes.csv")
        self.bank_rate = [(dt.date.fromisoformat(a), b) for a, b in br.itertuples(index=False)]
        self.wx_uk = self._weather("ghcnd_bradford_daily.csv", 101)
        self.wx_bd = self._weather("ghcnd_dhaka_daily.csv", 102)

    @staticmethod
    def _weather(name: str, seed: int) -> dict[dt.date, tuple[float, float, float, bool]]:
        df = pd.read_csv(ANCHORS / name)
        df["d"] = pd.to_datetime(df["date"])
        df["doy"] = df["d"].dt.dayofyear
        clim = df.groupby("doy")[["tmax_c", "tmin_c", "prcp_mm"]].mean()
        clim = clim.reindex(range(1, 367)).interpolate(limit_direction="both")
        clim = clim.rolling(7, center=True, min_periods=1).mean()
        have = {r.d.date(): (r.tmax_c, r.tmin_c, r.prcp_mm) for r in df.itertuples()}
        rg = np.random.default_rng(seed)
        out = {}
        for d in days(D0 - dt.timedelta(days=45), D1 + dt.timedelta(days=45)):
            doy = d.timetuple().tm_yday
            c = clim.loc[doy]
            tx, tn, pr = have.get(d, (None, None, None))
            filled = False
            if tx is None or tx != tx:
                tx, filled = float(c.tmax_c + rg.normal(0, 2.5)), True
            if tn is None or tn != tn:
                tn, filled = float(c.tmin_c + rg.normal(0, 2.0)), True
            if pr is None or pr != pr:
                p_wet = min(0.9, float(c.prcp_mm) / 6.0 + 0.05)
                pr, filled = (float(rg.exponential(max(c.prcp_mm / max(p_wet, 0.05), 0.5))) if rg.random() < p_wet else 0.0), True
            out[d] = (float(tx), float(tn), float(pr), filled)
        return out

    # fx helpers -------------------------------------------------------------------
    def cny_per_usd(self, d: dt.date) -> float:
        return self.fx[d][0]

    def usd_per_eur(self, d: dt.date) -> float:
        return self.fx[d][1]

    def usd_per_gbp(self, d: dt.date) -> float:
        return self.fx[d][2]

    def cny_per(self, ccy: str, d: dt.date) -> float:
        c = self.fx[d]
        return {"CNY": 1.0, "USD": c[0], "EUR": c[0] * c[1], "GBP": c[0] * c[2]}[ccy]

    def gscpi(self, d: dt.date) -> float:
        return float(self.gscpi_m.get(ym(d), 0.0))

    def bdt_per_usd(self, d: dt.date) -> float:
        return float(self.bdt.get(ym(d), 122.0))

    def commodity_index(self, col: str, d: dt.date) -> float:
        """Pink-sheet series relative to its 2020-2026 average (>1 = expensive)."""
        s = self.pink[col].dropna()
        return float(self.pink[col].get(ym(d), s.iloc[-1]) / s.mean())

    def wfp_index(self, d: dt.date, commodities: tuple[str, ...]) -> float:
        w = self.wfp[self.wfp.commodity.isin(commodities)]
        piv = w.pivot_table(index="month", columns="commodity", values="price_bdt")
        norm = (piv / piv.mean()).mean(axis=1)
        m = ym(d)
        return float(norm.get(m, norm.iloc[-1])) if len(norm) else 1.0

    def uk_bank_rate(self, d: dt.date) -> float:
        r = 0.75
        for dd, v in self.bank_rate:
            if dd <= d:
                r = v
        return r


# ----------------------------------------------------------------------------- ledger model
@dataclass
class Acct:
    key: str
    code: str
    name: str
    type: str
    subtype: str
    ccy: str

    @property
    def is_stmt(self) -> bool:
        return self.subtype in ("cash", "bank", "wallet")


@dataclass
class Line:
    acct: str
    dr: int
    cr: int
    ccy: str
    fx: float
    base: int  # signed (dr - cr) in base currency
    cp: str | None = None
    memo: str | None = None

    @property
    def nat(self) -> int:
        return self.dr - self.cr


@dataclass
class Entry:
    id: str
    date: dt.date
    ref: str
    memo: str
    lines: list[Line]
    kind: str
    tags: dict = field(default_factory=dict)

    def sig(self) -> list[tuple]:
        return [(self.date.isoformat(), l.acct, l.ccy, l.nat, l.base) for l in self.lines]


class World:
    """One company's truth. `post` builds balanced entries and tracks balances."""

    def __init__(self, cid: str, base: str, accts: list[Acct], fx: Callable[[str, dt.date], float] | None = None,
                 fx_acct: str | None = None) -> None:
        self.cid, self.base = cid, base
        self.accts = {a.key: a for a in accts}
        self.fx = fx or (lambda ccy, d: 1.0)
        self.fx_acct = fx_acct
        self.entries: list[Entry] = []
        self.nat: dict[str, int] = defaultdict(int)
        self.bs: dict[str, int] = defaultdict(int)
        self.n = 0
        self.meta: dict = {}

    def bal(self, acct: str) -> int:
        """Native balance (account currency)."""
        return self.nat[acct]

    def _foreign_monetary(self, a: Acct) -> bool:
        return a.ccy != self.base and a.type in ("asset", "liability")

    def post(self, d: dt.date, spec: list[tuple], ref: str = "", memo: str = "", kind: str = "", tags: dict | None = None) -> Entry:
        """spec items: (acct, signed_native[, ccy[, counterparty[, line_memo]]]); + = debit, - = credit."""
        self.n += 1
        eid = f"je-{self.cid}-{self.n:06d}"
        lines: list[Line] = []
        for item in spec:
            acct, amt = item[0], int(item[1])
            if amt == 0:
                continue
            a = self.accts[acct]
            ccy = item[2] if len(item) > 2 and item[2] else a.ccy
            cp = item[3] if len(item) > 3 else None
            lm = item[4] if len(item) > 4 else None
            rate = self.fx(ccy, d)
            if ccy == self.base:
                base_amt, rate = amt, 1.0
            else:
                nb = self.nat[acct]
                if self._foreign_monetary(a) and ccy == a.ccy and nb != 0 and (nb > 0) != (amt > 0):
                    # relief of a foreign monetary balance at its carrying (average) rate
                    base_amt = -self.bs[acct] if amt == -nb else rnd(amt * self.bs[acct] / nb)
                    rate = base_amt / amt
                else:
                    base_amt = rnd(amt * rate)
            lines.append(Line(acct, max(amt, 0), max(-amt, 0), ccy, round(rate, 8), base_amt, cp, lm))
        total = sum(l.base for l in lines)
        if total != 0:
            if not self.fx_acct:
                raise ValueError(f"unbalanced entry {eid} {kind} {memo}: {total}")
            lines.append(Line(self.fx_acct, max(-total, 0), max(total, 0), self.base, 1.0, -total, None, "FX difference on settlement"))
        e = Entry(eid, d, ref, memo, lines, kind, dict(tags or {}))
        for l in lines:
            a = self.accts[l.acct]
            self.bs[l.acct] += l.base
            if l.ccy == a.ccy:
                self.nat[l.acct] += l.nat
        self.entries.append(e)
        return e

    def totals_by_month(self) -> dict[tuple[str, str], int]:
        """(month, account) -> base amount (signed dr-cr)."""
        out: dict[tuple[str, str], int] = defaultdict(int)
        for e in self.entries:
            for l in e.lines:
                out[(ym(e.date), l.acct)] += l.base
        return out


class Book:
    """The owner's books: starts as a copy of the truth; problems edit it."""

    def __init__(self, world: World) -> None:
        self.w = world
        self.entries: dict[str, Entry] = {}
        self.order: list[str] = []
        self.added = 0
        for e in world.entries:
            b = copy.deepcopy(e)
            b.id = e.id.replace("je-", "bk-", 1)
            b.tags["truth_id"] = e.id
            self.entries[b.id] = b
            self.order.append(b.id)

    @staticmethod
    def bid(truth_id: str) -> str:
        return truth_id.replace("je-", "bk-", 1)

    def get(self, truth_id: str) -> Entry | None:
        return self.entries.get(self.bid(truth_id))

    def drop(self, truth_id: str) -> None:
        bid = self.bid(truth_id)
        del self.entries[bid]
        self.order.remove(bid)

    def alter(self, truth_id: str, *, date: dt.date | None = None, lines: list[Line] | None = None, memo: str | None = None) -> Entry:
        e = self.entries[self.bid(truth_id)]
        if date:
            e.date = date
        if lines is not None:
            e.lines = lines
        if memo is not None:
            e.memo = memo
        return e

    def add(self, d: dt.date, spec: list[tuple], ref: str = "", memo: str = "", kind: str = "", tags: dict | None = None) -> Entry:
        """Add a book-only entry (no truth counterpart). Uses the world's fx for line construction."""
        self.added += 1
        tmp = World(self.w.cid, self.w.base, list(self.w.accts.values()), self.w.fx, self.w.fx_acct)
        tmp.n = 0
        e = tmp.post(d, spec, ref, memo, kind, tags)
        e.id = f"bk-{self.w.cid}-A{self.added:05d}"
        self.entries[e.id] = e
        self.order.append(e.id)
        return e

    def make_line(self, acct: str, amt: int, d: dt.date, ccy: str | None = None) -> Line:
        a = self.w.accts[acct]
        ccy = ccy or a.ccy
        rate = 1.0 if ccy == self.w.base else self.w.fx(ccy, d)
        return Line(acct, max(amt, 0), max(-amt, 0), ccy, round(rate, 8), rnd(amt * rate))

    def sorted_entries(self) -> list[Entry]:
        return sorted(self.entries.values(), key=lambda e: (e.date, e.id))


class Problems:
    """Registry of planted problems and honest noise; the fix is always 'void book entries + add truth entries'."""

    def __init__(self, cid: str, world: World, book: Book) -> None:
        self.cid, self.w, self.b = cid, world, book
        self.items: list[dict] = []
        self.noise: list[dict] = []
        self.tmap = {e.id: e for e in world.entries}

    def plant(self, ptype: str, d: dt.date, desc: str, *, truth: Iterable[str] = (), book: Iterable[str] = (),
              amount: int | None = None, ccy: str | None = None, subtype: str | None = None, **extra) -> str:
        pid = f"{self.cid}-P{len(self.items) + 1:05d}"
        self.items.append(dict(id=pid, type=ptype, subtype=subtype, date=d, description=desc,
                               truth_ids=list(truth), book_ids=list(book), amount=amount, ccy=ccy, extra=extra))
        return pid

    def note(self, nid: str, d: dt.date, desc: str, why: str, truth: Iterable[str]) -> None:
        self.noise.append(dict(id=f"{self.cid}-N{len(self.noise) + 1:05d}", date=d.isoformat(), description=desc,
                               why_not_a_problem=why, truth_ids=list(truth), kind=nid))

    # ---- derived facts ----
    def fix_ops(self, p: dict) -> dict:
        add = []
        for tid in p["truth_ids"]:
            t = self.tmap[tid]
            add.append(entry_json(t, self.w))
        return {"void_book_entries": list(p["book_ids"]), "add_entries": add}

    def delta_lines(self, p: dict) -> list[tuple]:
        """(date, acct, ccy, native, base) of (book version - truth version)."""
        out = []
        for bid in p["book_ids"]:
            e = self.b.entries[bid]
            out += [(e.date, l.acct, l.ccy, l.nat, l.base) for l in e.lines]
        for tid in p["truth_ids"]:
            t = self.tmap[tid]
            out += [(t.date, l.acct, l.ccy, -l.nat, -l.base) for l in t.lines]
        return out


def entry_json(e: Entry, w: "World | None" = None) -> dict:
    return {"date": e.date.isoformat(), "reference": e.ref, "memo": e.memo, "lines": [
        {"account": (f"{w.cid}-{w.accts[l.acct].code}" if w else l.acct), "debit": money(l.dr), "credit": money(l.cr), "currency": l.ccy, "fx_rate": l.fx, "amount_base": money(l.base)}
        for l in e.lines]}


# ----------------------------------------------------------------------------- deterministic file writers
def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_csv(path: Path, header: list[str], rows: Iterable[list], gz: bool = False, bom: bool = False) -> int:
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    n = 0
    for r in rows:
        w.writerow(r)
        n += 1
    data = buf.getvalue().encode("utf-8-sig" if bom else "utf-8")
    if gz:
        with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as g:
            g.write(data)
    else:
        path.write_bytes(data)
    return n


def write_json(path: Path, obj) -> None:
    write_text(path, json.dumps(obj, indent=1, sort_keys=True, default=str, ensure_ascii=False) + "\n")


def save_xlsx(wb, path: Path) -> None:
    """Save an openpyxl workbook with fixed timestamps so reruns are byte-identical."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.properties.created = dt.datetime(2026, 10, 1)
    wb.properties.modified = dt.datetime(2026, 10, 1)
    wb.properties.creator = "synthetic-data-generator"
    wb.properties.lastModifiedBy = "synthetic-data-generator"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    with zipfile.ZipFile(buf) as zin, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in sorted(zin.namelist()):
            zi = zipfile.ZipInfo(item, date_time=(2026, 10, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o600 << 16
            data = zin.read(item)
            if item == "docProps/core.xml":
                import re
                data = re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*(</dcterms:(?:created|modified)>)", rb"\g<1>2026-10-01T00:00:00Z\g<2>", data)
            zout.writestr(zi, data)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


class Trace:
    """Which truth/book/problem ids each source-export row came from."""

    def __init__(self) -> None:
        self.rows: list[tuple] = []  # (file, row_id, ref_kind, ref_id)
        self.by_ref: dict[str, list[tuple[str, str]]] = defaultdict(list)

    def add(self, file: str, row_id: str, kind: str, ref: str) -> None:
        self.rows.append((file, str(row_id), kind, ref))
        self.by_ref[ref].append((file, str(row_id)))
