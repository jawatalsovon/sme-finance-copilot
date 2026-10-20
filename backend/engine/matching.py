"""Book-vs-statement matching for one cash account (bank, wallet).

Plain words: we line up what the books say went in/out of an account with what the bank or
wallet statement says, in passes from the strictest to the loosest:

  1. exact      same day, same amount, one book item to one statement line
  2. window     same amount, dates up to WINDOW_DAYS apart (closest first)  -> timing check
  3. day_net    all still-open book items of a day sum to all still-open statement lines of that day
  4. day_sign   same, separately for money in and money out (daily totals, e.g. "bKash sales")
  5. residual   a day's totals differ by exactly one or two lines -> those lines are the exceptions
  6. near       same direction, a few days apart, amounts within NEAR_PCT -> an amount difference
               (fees taken off a payout, a different FX rate)
Whatever is left is unmatched: money on the statement that the books never recorded, or book
entries the statement cannot confirm.

Amounts are integer cents in the account's own currency. Book "items" are the net of one journal
entry on the account (so a sale and its card charge in one entry match one net bank credit).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

WINDOW_DAYS = 7
NEAR_DAYS = 3
NEAR_PCT = 0.15
SAME_DAY_PCT = 0.6


@dataclass
class MatchResult:
    book: pd.DataFrame          # book items + columns: m_kind, m_group, m_stmt (one stmt idx for 1:1)
    stmt: pd.DataFrame          # statement lines + columns: m_kind, m_group, m_book
    near_pairs: list[tuple[int, int]] = field(default_factory=list)   # (book idx, stmt idx)

    def unmatched_book(self) -> pd.DataFrame:
        return self.book[self.book["m_kind"] == ""]

    def unmatched_stmt(self) -> pd.DataFrame:
        return self.stmt[self.stmt["m_kind"] == ""]


def book_items(lines: pd.DataFrame) -> pd.DataFrame:
    """Net journal lines per (entry, account) -> one book item per entry."""
    if lines.empty:
        return pd.DataFrame(columns=["entry_id", "date", "amt", "line_ids", "memo", "reference", "month"])
    g = lines.groupby("entry_id", sort=False)
    items = pd.DataFrame({
        "entry_id": list(g.groups.keys()),
        "date": g["entry_date"].first().to_numpy(),
        "amt": g["native_c"].sum().to_numpy(),
        "line_ids": g["line_id"].apply(list).to_numpy(),
        "memo": g["entry_memo"].first().fillna("").to_numpy(),
        "reference": g["reference"].first().fillna("").to_numpy(),
    })
    items = items[items["amt"] != 0].reset_index(drop=True)
    items["month"] = [f"{d.year:04d}-{d.month:02d}" for d in items["date"]]
    return items


def match_account(book: pd.DataFrame, stmt: pd.DataFrame) -> MatchResult:
    """book: entry_id, date, amt (cents). stmt: id, date, amt (principal cents)."""
    b = book.copy().reset_index(drop=True)
    s = stmt.copy().reset_index(drop=True)
    for df, other in ((b, "m_stmt"), (s, "m_book")):
        df["m_kind"] = ""
        df["m_group"] = -1
        df[other] = -1
    gid = [0]

    def new_group() -> int:
        gid[0] += 1
        return gid[0]

    # 1. exact: same date + amount, 1:1 (k-th occurrence pairs with k-th occurrence)
    _pair_on(b, s, ["date", "amt"], "exact", new_group)

    # 2. window: same amount, |days| <= WINDOW_DAYS, closest first
    bo, so = b[b["m_kind"] == ""], s[s["m_kind"] == ""]
    if len(bo) and len(so):
        cand = bo[["date", "amt"]].reset_index().merge(so[["date", "amt"]].reset_index(), on="amt",
                                                       suffixes=("_b", "_s"))
        if len(cand):
            dd = (pd.to_datetime(cand["date_s"]) - pd.to_datetime(cand["date_b"])).dt.days
            cand["dd"] = dd.abs()
            cand = cand[cand["dd"] <= WINDOW_DAYS].sort_values(["dd", "index_b", "index_s"], kind="mergesort")
            used_b, used_s = set(), set()
            for ib, is_ in zip(cand["index_b"].to_numpy(), cand["index_s"].to_numpy()):
                if ib in used_b or is_ in used_s:
                    continue
                used_b.add(ib)
                used_s.add(is_)
                g = new_group()
                b.loc[ib, ["m_kind", "m_group", "m_stmt"]] = ["window", g, is_]
                s.loc[is_, ["m_kind", "m_group", "m_book"]] = ["window", g, ib]

    # 3. day net and 4. day by sign
    _group_equal(b, s, ["date"], "day_net", new_group)
    b["sign"] = np.sign(b["amt"])
    s["sign"] = np.sign(s["amt"])
    _group_equal(b, s, ["date", "sign"], "day_sign", new_group)

    # 5. residual: the day's totals differ by one or two lines
    bo, so = b[b["m_kind"] == ""], s[s["m_kind"] == ""]
    if len(bo) and len(so):
        bg = {k: v for k, v in bo.groupby(["date", "sign"]).groups.items()}
        sg = {k: v for k, v in so.groupby(["date", "sign"]).groups.items()}
        for key in bg.keys() & sg.keys():
            bi, si = list(bg[key]), list(sg[key])
            if len(bi) + len(si) > 400:
                continue
            r = int(s.loc[si, "amt"].sum() - b.loc[bi, "amt"].sum())
            if r == 0:
                continue
            side, idx = (s, si) if np.sign(r) == key[1] else (b, bi)
            target = abs(r)
            pick = _subset(side.loc[idx, "amt"].abs(), target, max_size=2)
            if pick is None:
                continue
            g = new_group()
            rest_b = [i for i in bi if not (side is b and i in pick)]
            rest_s = [i for i in si if not (side is s and i in pick)]
            if not rest_b or not rest_s:
                continue
            b.loc[rest_b, ["m_kind", "m_group"]] = ["residual", g]
            s.loc[rest_s, ["m_kind", "m_group"]] = ["residual", g]

    # 6. near: same direction, close dates, amount within NEAR_PCT
    near: list[tuple[int, int]] = []
    bo, so = b[b["m_kind"] == ""], s[s["m_kind"] == ""]
    if len(bo) and len(so) and len(bo) * len(so) < 5_000_000:
        cand = bo[["date", "amt", "sign"]].reset_index().merge(
            so[["date", "amt", "sign"]].reset_index(), on="sign", suffixes=("_b", "_s"))
        if len(cand):
            cand["dd"] = (pd.to_datetime(cand["date_s"]) - pd.to_datetime(cand["date_b"])).dt.days.abs()
            cand["rel"] = (cand["amt_s"] - cand["amt_b"]).abs() / cand["amt_b"].abs().clip(lower=1)
            cand = cand[(cand["dd"] <= NEAR_DAYS) & (cand["rel"] <= NEAR_PCT)]
            cand = cand.sort_values(["rel", "dd", "index_b", "index_s"], kind="mergesort")
            used_b, used_s = set(), set()
            for ib, is_ in zip(cand["index_b"].to_numpy(), cand["index_s"].to_numpy()):
                if ib in used_b or is_ in used_s:
                    continue
                used_b.add(ib)
                used_s.add(is_)
                g = new_group()
                b.loc[ib, ["m_kind", "m_group", "m_stmt"]] = ["near", g, is_]
                s.loc[is_, ["m_kind", "m_group", "m_book"]] = ["near", g, ib]
                near.append((int(ib), int(is_)))

    # 7. same day, one open item on each side in the same direction: a bigger difference is still
    #    the same transaction (e.g. a loan repayment that also took interest)
    bo, so = b[b["m_kind"] == ""], s[s["m_kind"] == ""]
    if len(bo) and len(so):
        bc = bo.groupby(["date", "sign"]).size()
        sc = so.groupby(["date", "sign"]).size()
        both = [k for k in bc.index.intersection(sc.index) if bc[k] == 1 and sc[k] == 1]
        for key in both:
            ib = bo[(bo["date"] == key[0]) & (bo["sign"] == key[1])].index[0]
            is_ = so[(so["date"] == key[0]) & (so["sign"] == key[1])].index[0]
            rel = abs(s.loc[is_, "amt"] - b.loc[ib, "amt"]) / max(1, abs(b.loc[ib, "amt"]))
            if rel > SAME_DAY_PCT:
                continue
            g = new_group()
            b.loc[ib, ["m_kind", "m_group", "m_stmt"]] = ["near", g, is_]
            s.loc[is_, ["m_kind", "m_group", "m_book"]] = ["near", g, ib]
            near.append((int(ib), int(is_)))
    return MatchResult(book=b, stmt=s, near_pairs=near)


def _pair_on(b: pd.DataFrame, s: pd.DataFrame, keys: list[str], kind: str, new_group) -> None:
    bo, so = b[b["m_kind"] == ""], s[s["m_kind"] == ""]
    if not len(bo) or not len(so):
        return
    bk = bo[keys].copy()
    bk["k"] = bk.groupby(keys).cumcount()
    sk = so[keys].copy()
    sk["k"] = sk.groupby(keys).cumcount()
    m = bk.reset_index().merge(sk.reset_index(), on=keys + ["k"], suffixes=("_b", "_s"))
    if not len(m):
        return
    groups = np.array([new_group() for _ in range(len(m))])
    ib, is_ = m["index_b"].to_numpy(), m["index_s"].to_numpy()
    b.loc[ib, "m_kind"] = kind
    b.loc[ib, "m_group"] = groups
    b.loc[ib, "m_stmt"] = is_
    s.loc[is_, "m_kind"] = kind
    s.loc[is_, "m_group"] = groups
    s.loc[is_, "m_book"] = ib


def _group_equal(b: pd.DataFrame, s: pd.DataFrame, keys: list[str], kind: str, new_group) -> None:
    bo, so = b[b["m_kind"] == ""], s[s["m_kind"] == ""]
    if not len(bo) or not len(so):
        return
    bs = bo.groupby(keys)["amt"].agg(["sum", "size"])
    ss = so.groupby(keys)["amt"].agg(["sum", "size"])
    j = bs.join(ss, lsuffix="_b", rsuffix="_s", how="inner")
    j = j[(j["sum_b"] == j["sum_s"])]
    if not len(j):
        return
    ok = set(j.index)
    kb = list(bo[keys].itertuples(index=False, name=None)) if len(keys) > 1 else list(bo[keys[0]])
    ks = list(so[keys].itertuples(index=False, name=None)) if len(keys) > 1 else list(so[keys[0]])
    gmap = {k: new_group() for k in ok}
    bidx = [i for i, k in zip(bo.index, kb) if k in ok]
    sidx = [i for i, k in zip(so.index, ks) if k in ok]
    b.loc[bidx, "m_kind"] = kind
    b.loc[bidx, "m_group"] = [gmap[k] for k in kb if k in ok]
    s.loc[sidx, "m_kind"] = kind
    s.loc[sidx, "m_group"] = [gmap[k] for k in ks if k in ok]


def _subset(values: pd.Series, target: int, max_size: int = 2) -> set | None:
    """Indices of 1..max_size values summing exactly to target (smallest subset first)."""
    vals = list(values.items())
    for i, v in vals:
        if v == target:
            return {i}
    if max_size >= 2 and len(vals) <= 200:
        for (i, v), (j, w) in combinations(vals, 2):
            if v + w == target:
                return {i, j}
    return None
