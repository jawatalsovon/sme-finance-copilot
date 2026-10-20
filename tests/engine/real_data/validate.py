"""Engine validation on public real data (see docs/research/REAL_DATA_VALIDATION.md).

    python -m tests.engine.real_data.validate            # all datasets present -> results JSON + table

V1 UCI Online Retail II  weekly product revenue   rolling-origin backtest, engine vs. seasonal naive
V4 Monash Car Parts      200 monthly series        last 12 months, MASE, engine vs. naive baselines
V2 Bexley payments >£500 3 months, 18,719 rows     duplicate-payment scan: natural candidates for audit
                                                   + recall/precision on 350 INJECTED errors (seed 42)
Only public files under data/raw/ are read. Supplier names are pseudonymised in every output.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from backend.engine.duplicates import ALL_RULES, CORE_RULES, payment_duplicates
from backend.engine.forecast import forecast_series, smape

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw"
V1_XLSX = RAW / "uci_online_retail2" / "online_retail_II.xlsx"
V1_WEEKLY = RAW / "uci_online_retail2" / "weekly_revenue_derived.csv"
V4_TSF = RAW / "monash_zenodo" / "car_parts_dataset_with_missing_values.tsf"
V2_DIR = RAW / "bexley_spend_over_500"
SEED = 42
NON_PRODUCT = {"POST", "DOT", "M", "BANK CHARGES", "AMAZONFEE", "D", "S", "CRUK", "ADJUST", "ADJUST2", "B",
               "TEST001", "TEST002", "C2", "PADS"}


# ---------------------------------------------------------------------------- V1
def v1_weekly_from_source() -> pd.Series:
    """Weekly product revenue recomputed from the original workbook (non-cancellation invoices,
    product lines only), weeks ending Sunday, first and last partial weeks dropped."""
    frames = pd.read_excel(V1_XLSX, sheet_name=None, dtype={"Invoice": str, "StockCode": str})
    d = pd.concat(frames.values(), ignore_index=True)
    d = d[~d["Invoice"].str.startswith("C") & ~d["StockCode"].str.upper().isin(NON_PRODUCT)]
    d["rev"] = d["Quantity"] * d["Price"]
    w = d.set_index("InvoiceDate")["rev"].resample("W-SUN").sum()
    return w.iloc[1:-1]


def v1_backtest(weekly: pd.Series) -> dict:
    """Same protocols as R1's baselines: last 13 / last 26 weeks held out, and rolling 4-week-ahead over
    the last 26 origins. Engine = the production ensemble (holiday factors for GB)."""
    y = weekly.copy()
    y.index = y.index - pd.Timedelta(days=6)        # engine labels a week by its Monday
    out = {"weeks": int(len(y)), "first_week_start": str(y.index[0].date()), "last_week_start": str(y.index[-1].date())}

    def one(origin: int, h: int) -> dict:
        fc = forecast_series(y.iloc[:origin], h, "week", "GB")
        a = y.iloc[origin:origin + h].to_numpy()
        sn = y.iloc[origin - 52:origin - 52 + h].to_numpy()          # R1's seasonal naive (lag 52, unscaled)
        return {"actual": a, "engine": fc["ensemble"], "sn": sn, "models": fc}

    for h in (13, 26):
        r = one(len(y) - h, h)
        out[f"holdout_{h}"] = {"engine_smape": smape(r["actual"], r["engine"]),
                               "seasonal_naive_smape": smape(r["actual"], r["sn"]),
                               "per_model_smape": {k: smape(r["actual"], v) for k, v in r["models"].items()}}
    eng, sn = [], []
    for origin in range(len(y) - 26 - 3, len(y) - 3):          # 26 origins, 4 weeks ahead each
        r = one(origin, 4)
        eng.append(smape(r["actual"], r["engine"]))
        sn.append(smape(r["actual"], r["sn"]))
    out["rolling4"] = {"origins": len(eng), "engine_smape": float(np.mean(eng)), "seasonal_naive_smape": float(np.mean(sn))}
    return out


# ---------------------------------------------------------------------------- V4
def read_tsf(path: Path) -> list[tuple[str, np.ndarray]]:
    rows, data = [], False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not data:
            data = line.strip().lower() == "@data"
            continue
        if not line.strip():
            continue
        name, _start, vals = line.split(":", 2)
        rows.append((name, np.array([np.nan if v == "?" else float(v) for v in vals.split(",")])))
    return rows


def v4_backtest(n_sample: int = 200, h: int = 12) -> dict:
    """Sample `n_sample` complete series (fixed seed), hold out the last 12 months, MASE scaled by the
    in-sample one-step naive error (the usual choice for intermittent demand)."""
    allrows = read_tsf(V4_TSF)
    complete = [(n, v) for n, v in allrows if not np.isnan(v).any()]
    rng = np.random.default_rng(SEED)
    pick = rng.choice(len(complete), size=min(n_sample, len(complete)), replace=False)
    res = {"engine": [], "seasonal_naive": [], "naive": [], "mean12": [], "zero": []}
    beats_sn = beats_naive = ties = 0
    used = 0
    for i in sorted(pick):
        name, v = complete[i]
        train, test = v[:-h], v[-h:]
        scale = np.mean(np.abs(np.diff(train)))
        if scale == 0:
            continue                                   # constant history: MASE undefined
        used += 1
        idx = pd.date_range("1998-01-01", periods=len(train), freq="MS")
        fc = forecast_series(pd.Series(train, index=idx), h, "month")["ensemble"]
        preds = {"engine": fc, "seasonal_naive": train[-12:], "naive": np.repeat(train[-1], h),
                 "mean12": np.repeat(train[-12:].mean(), h), "zero": np.zeros(h)}
        mase = {k: float(np.mean(np.abs(test - p)) / scale) for k, p in preds.items()}
        for k in res:
            res[k].append(mase[k])
        if abs(mase["engine"] - mase["seasonal_naive"]) < 1e-12:
            ties += 1
        beats_sn += mase["engine"] < mase["seasonal_naive"] - 1e-12
        beats_naive += mase["engine"] < mase["naive"] - 1e-12
    return {"series_total": len(allrows), "series_complete": len(complete), "sampled": int(len(pick)),
            "evaluated": used, "horizon_months": h,
            "median_mase": {k: float(np.median(v)) for k, v in res.items()},
            "mean_mase": {k: float(np.mean(v)) for k, v in res.items()},
            "share_engine_beats_seasonal_naive": beats_sn / used if used else None,
            "share_engine_beats_naive": beats_naive / used if used else None, "ties_with_seasonal_naive": ties}


# ---------------------------------------------------------------------------- V2
def v2_load() -> pd.DataFrame:
    frames = []
    for p in sorted(V2_DIR.glob("*.csv")):
        d = pd.read_csv(p, skiprows=1, dtype={"Transaction Number": str, "Supplier Number": str}, encoding="latin-1")
        d["source_file"] = p.name
        d["source_row"] = np.arange(len(d)) + 3            # file line number (title = 1, header = 2)
        frames.append(d)
    d = pd.concat(frames, ignore_index=True)
    d = d.dropna(subset=["Payment Date", "Amount", "Supplier"])
    d["date"] = pd.to_datetime(d["Payment Date"], dayfirst=True).dt.date
    d["amount"] = pd.to_numeric(d["Amount"].astype(str).str.replace(",", ""), errors="coerce")
    d = d.dropna(subset=["amount"])
    # one supplier = one normalised name (the same supplier can appear under several supplier numbers)
    d["supplier_id"] = d["Supplier"].astype(str).str.upper().str.replace(r"\s+", " ", regex=True).str.strip()
    d["doc_no"] = d["Transaction Number"].astype(str)
    d["id"] = [f"bx-{f}-{r}" for f, r in zip(d["source_file"], d["source_row"])]
    return d[["id", "date", "supplier_id", "Supplier", "amount", "doc_no", "Expense Type", "source_file", "source_row"]]


def pseudonym(supplier_id: str) -> str:
    return "SUP-" + hashlib.sha256(f"bexley|{supplier_id}".encode()).hexdigest()[:6].upper()


def inject(d: pd.DataFrame, seed: int = SEED) -> tuple[pd.DataFrame, pd.DataFrame]:
    """350 INJECTED errors on the real rows: 150 exact repeats, 100 near repeats (1-7 days later),
    50 amount typos (same day, amount off by 1-10%), 50 credit-note reversals (negative, 1-30 days later)."""
    rng = np.random.default_rng(seed)
    plan = [("exact_repeat", 150), ("near_repeat", 100), ("amount_typo", 50), ("reversal", 50)]
    src = d.sample(n=sum(n for _, n in plan), random_state=seed).reset_index(drop=True)
    new, key = [], []
    k = 0
    for kind, n in plan:
        for _ in range(n):
            r = src.iloc[k].copy()
            k += 1
            orig_id = r["id"]
            r["id"] = f"inj-{kind}-{k:03d}"
            r["doc_no"] = f"INJ{k:05d}"
            if kind == "near_repeat":
                r["date"] = r["date"] + pd.Timedelta(days=int(rng.integers(1, 8)))
            elif kind == "amount_typo":
                f = float(rng.uniform(0.01, 0.10)) * (1 if rng.random() < 0.5 else -1)
                r["amount"] = round(r["amount"] * (1 + f), 2)
            elif kind == "reversal":
                r["date"] = r["date"] + pd.Timedelta(days=int(rng.integers(1, 31)))
                r["amount"] = -r["amount"]
            new.append(r)
            key.append({"id": r["id"], "kind": kind, "original_id": orig_id})
    return pd.concat([d, pd.DataFrame(new)], ignore_index=True), pd.DataFrame(key)


def schedule_ids(d: pd.DataFrame) -> set[str]:
    months = d.assign(m=[x.strftime("%Y-%m") for x in d["date"]]).groupby(["supplier_id", "amount"])["m"].nunique()
    sched = set(months[months >= 2].index)
    return {i for i, s, a in zip(d["id"], d["supplier_id"], d["amount"]) if (s, abs(a)) in sched}


def baseline_r0(d: pd.DataFrame) -> list[tuple[str, str]]:
    """R0: same date + supplier + amount (every extra row paired with the first)."""
    pairs = []
    for _, g in d.groupby(["date", "supplier_id", "amount"]):
        if len(g) > 1:
            ids = list(g.sort_values("doc_no")["id"])
            pairs += [(ids[0], x) for x in ids[1:]]
    return pairs


def baseline_r1(d: pd.DataFrame) -> list[tuple[str, str]]:
    """R1: R0 minus supplier+amount pairs that recur in at least 2 calendar months (schedules)."""
    months = d.assign(m=[x.strftime("%Y-%m") for x in d["date"]]).groupby(["supplier_id", "amount"])["m"].nunique()
    sched = set(months[months >= 2].index)
    keep = {i for i, s, a in zip(d["id"], d["supplier_id"], d["amount"]) if (s, a) not in sched}
    return [(a, b) for a, b in baseline_r0(d) if a in keep and b in keep]


def score_pairs(pairs: list[tuple[str, str]], key: pd.DataFrame, sched: set[str] | None = None) -> dict:
    inj = dict(zip(key["id"], key["kind"]))
    hit_ids = {x for p in pairs for x in p if x in inj}
    tp = sum(1 for p in pairs if p[0] in inj or p[1] in inj)
    per_kind = {}
    for kind, g in key.groupby("kind"):
        per_kind[kind] = {"injected": int(len(g)), "found": int(g["id"].isin(hit_ids).sum()),
                          "recall": float(g["id"].isin(hit_ids).mean())}
    split = {}
    if sched is not None:   # recall on copies of regular (schedule) payments vs. one-off payments
        k2 = key.assign(of_schedule=key["original_id"].isin(sched))
        for flag, g in k2.groupby("of_schedule"):
            split["copies_of_schedule_payments" if flag else "copies_of_one_off_payments"] = {
                "injected": int(len(g)), "recall": float(g["id"].isin(hit_ids).mean())}
    return {"flagged_pairs": len(pairs), "pairs_with_injected_row": tp, "recall_split": split,
            "precision_strict": tp / len(pairs) if pairs else None,
            "recall": float(key["id"].isin(hit_ids).mean()), "per_kind": per_kind}


def v2_duplicates() -> dict:
    d = v2_load()
    n = len(d)
    dj, key = inject(d)
    sched = schedule_ids(d)
    sup_of = dict(zip(d["id"], d["supplier_id"]))
    exp_of = dict(zip(d["id"], d["Expense Type"]))
    res = {"rows": n, "suppliers": int(d["supplier_id"].nunique()), "dates": [str(min(d["date"])), str(max(d["date"]))],
           "share_rows_in_schedules": len(sched) / n,
           "baselines_untouched": {"r0_pairs": len(baseline_r0(d)), "r0_flags_per_1000": len(baseline_r0(d)) / n * 1000,
                                   "r1_pairs": len(baseline_r1(d)), "r1_flags_per_1000": len(baseline_r1(d)) / n * 1000},
           "injected_baselines": {"seed": SEED, "r0": score_pairs(baseline_r0(dj), key, sched),
                                  "r1": score_pairs(baseline_r1(dj), key, sched)}}
    for label, rules in (("engine_core", CORE_RULES), ("engine_plus_schedule_rule", ALL_RULES)):
        natural = payment_duplicates(d, rules)
        found = payment_duplicates(dj, rules)
        res[label] = {"rules": list(rules), "untouched_candidates": int(len(natural)),
                      "untouched_by_rule": natural["rule"].value_counts().to_dict(),
                      "untouched_flags_per_1000": len(natural) / n * 1000,
                      "injected": score_pairs(list(zip(found["id_a"], found["id_b"])), key, sched)}
        if label == "engine_core":
            top = natural.assign(_abs=natural["amount_b"].abs()).sort_values(["_abs", "id_b"],
                                                                              ascending=[False, True]).head(50)
            res["audit_top50"] = [{"rank": i + 1, "rule": r.rule, "supplier": pseudonym(sup_of[r.id_a]),
                                   "expense_type": exp_of[r.id_a], "date_a": str(r.date_a), "date_b": str(r.date_b),
                                   "amount_a": r.amount_a, "amount_b": r.amount_b, "row_a": r.id_a, "row_b": r.id_b}
                                  for i, r in enumerate(top.itertuples())]
    return res


# ---------------------------------------------------------------------------- main
def main(argv: list[str]) -> dict:
    out: dict = {}
    if V1_WEEKLY.is_file() or V1_XLSX.is_file():
        weekly = None
        if V1_XLSX.is_file() and "--fast" not in argv:
            weekly = v1_weekly_from_source()
            if V1_WEEKLY.is_file():
                r1 = pd.read_csv(V1_WEEKLY, parse_dates=["InvoiceDate"]).set_index("InvoiceDate")["revenue_gbp"]
                j = pd.concat([weekly.rename("ours"), r1.rename("r1")], axis=1).dropna()
                out["v1_series_check"] = {"weeks_compared": int(len(j)),
                                          "max_abs_diff_gbp": float((j["ours"] - j["r1"]).abs().max())}
        if weekly is None:
            weekly = pd.read_csv(V1_WEEKLY, parse_dates=["InvoiceDate"]).set_index("InvoiceDate")["revenue_gbp"]
        out["v1"] = v1_backtest(weekly)
    if V4_TSF.is_file():
        out["v4"] = v4_backtest()
    if V2_DIR.is_dir() and list(V2_DIR.glob("*.csv")):
        out["v2"] = v2_duplicates()
    dest = ROOT / "docs" / "validation" / "real_data_results.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "v2"} | (
        {"v2": {kk: vv for kk, vv in out["v2"].items() if kk != "audit_top50"}} if "v2" in out else {}),
        indent=1, default=str))
    return out


if __name__ == "__main__":
    main(sys.argv[1:])
