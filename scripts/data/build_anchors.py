"""Process the raw public downloads in data/raw/ into small CSV anchors in data/anchors/.

Self-contained on purpose (no sibling imports) so it can be run in Python isolated mode,
which is how files downloaded from the internet must be read:

    .venv/Scripts/python -I scripts/data/build_anchors.py            # all small series
    .venv/Scripts/python -I scripts/data/build_anchors.py --basket   # + UCI basket shapes (slow, ~3 min)

Every output is a derived series from a public dataset listed in docs/research/DATASETS.md.
Licences and sources: data/raw/MANIFEST.md and docs/LICENSES_DATA.md.
The DOWNLOAD step is documented in data/raw/MANIFEST.md (curl commands, one folder per source).
"""
from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "anchors"
START, END = "2019-12-01", "2026-09-30"


def _save(df: pd.DataFrame, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / name, index=False, lineterminator="\n")
    print(f"  wrote data/anchors/{name}: {len(df)} rows")


def fx_daily() -> None:
    """Fed H.10 (via FRED): CNY per USD, USD per EUR, USD per GBP -> daily, forward-filled."""
    parts = []
    for sid in ("DEXCHUS", "DEXUSEU", "DEXUSUK"):
        d = pd.read_csv(RAW / "fred_h10" / f"fx_{sid}.csv", na_values=["."])
        d.columns = ["date", sid]
        d["date"] = pd.to_datetime(d["date"])
        parts.append(d.set_index("date")[sid])
    df = pd.concat(parts, axis=1).sort_index()
    idx = pd.date_range("2019-12-01", END, freq="D")
    df = df.reindex(idx).ffill().bfill()
    df = df.rename(columns={"DEXCHUS": "cny_per_usd", "DEXUSEU": "usd_per_eur", "DEXUSUK": "usd_per_gbp"})
    df = df.loc["2020-01-01":END].round(5)
    df.index.name = "date"
    _save(df.reset_index().assign(date=lambda x: x["date"].dt.strftime("%Y-%m-%d")), "fx_h10_daily.csv")


def bdt_usd_monthly() -> None:
    """BDT per USD: WDI annual period averages (PA.NUS.FCRF), interpolated to months.

    MODELLED, not observed: annual averages are placed at mid-year and linearly interpolated.
    2026 has no annual value yet; it is held flat at 2025 + 0.5 per month drift capped (flag modelled=2026).
    """
    j = json.loads((RAW / "worldbank_wdi" / "wdi_fx.json").read_text(encoding="utf-8"))
    rows = [(int(r["date"]), r["value"]) for r in j[1] if r["countryiso3code"] == "BGD" and r["value"]]
    ann = pd.Series(dict(rows)).sort_index()
    pts = {pd.Timestamp(year=y, month=7, day=1): v for y, v in ann.items()}
    last_y = max(ann.index)
    if last_y < 2026:
        pts[pd.Timestamp(year=2026, month=7, day=1)] = ann.iloc[-1]
    s = pd.Series(pts).sort_index()
    months = pd.date_range("2019-12-01", END, freq="MS")
    s = s.reindex(s.index.union(months)).interpolate(method="time", limit_direction="both")
    out = s.reindex(months).round(4).rename("bdt_per_usd").to_frame()
    out["modelled"] = 1
    out.index.name = "month"
    _save(out.reset_index().assign(month=lambda x: x["month"].dt.strftime("%Y-%m")), "bdt_usd_monthly_modelled.csv")
    cpi = json.loads((RAW / "worldbank_wdi" / "wdi_cpi.json").read_text(encoding="utf-8"))
    rows = [{"country": r["countryiso3code"], "year": int(r["date"]), "cpi_inflation_pct": r["value"]}
            for r in cpi[1] if r["value"] is not None]
    _save(pd.DataFrame(rows).sort_values(["country", "year"]), "wdi_cpi_inflation_annual.csv")


def gscpi() -> None:
    df = pd.read_excel(RAW / "nyfed_gscpi" / "gscpi_data.xlsx", sheet_name="GSCPI Monthly Data", header=None, usecols=[0, 1])
    df.columns = ["date", "gscpi"]
    df = df.iloc[1:]
    df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce")
    df = df.dropna(subset=["date"])
    df["gscpi"] = df["gscpi"].astype(float)
    df["month"] = df["date"].dt.strftime("%Y-%m")
    df = df[df["month"] >= "2019-12"]
    _save(df[["month", "gscpi"]].round(4), "gscpi_monthly.csv")


def pinksheet() -> None:
    x = pd.read_excel(RAW / "worldbank_pinksheet" / "CMO-Historical-Data-Monthly.xlsx", sheet_name="Monthly Prices", header=None)
    names = [str(v).strip() for v in x.iloc[4, :]]
    d = x.iloc[6:, :].copy()
    d.columns = names
    d = d.rename(columns={"nan": "period"})
    d["period"] = d.iloc[:, 0].astype(str)
    d = d[d["period"].str.match(r"^\d{4}M\d{2}$")]
    d["month"] = d["period"].str.slice(0, 4) + "-" + d["period"].str.slice(5, 7)
    want = {"Crude oil, Brent": "brent_usd_bbl", "Palm oil": "palm_oil_usd_mt", "Soybean oil": "soybean_oil_usd_mt",
            "Sugar, world": "sugar_world_usd_kg", "Wheat, US HRW": "wheat_us_hrw_usd_mt",
            "Rice, Thai 5%": "rice_thai5_usd_mt", "Tea, avg 3 auctions": "tea_avg3_usd_kg", "Cotton, A Index": "cotton_a_usd_kg"}
    keep = d[["month"] + list(want)].rename(columns=want)
    keep = keep[keep["month"] >= "2019-12"].reset_index(drop=True)
    for c in keep.columns[1:]:
        keep[c] = pd.to_numeric(keep[c], errors="coerce")
    _save(keep.round(4), "pinksheet_monthly.csv")


def wfp() -> None:
    w = pd.read_csv(RAW / "wfp_bgd" / "wfp_food_prices_bgd.csv", skiprows=[1])
    w["price"] = pd.to_numeric(w["price"], errors="coerce")
    w["usdprice"] = pd.to_numeric(w["usdprice"], errors="coerce")
    w = w[(w["date"] >= "2019-06-01") & (w["pricetype"].str.lower() == "retail")]
    w.loc[(w["commodity"] == "Wheat flour") & (w["unit"] == "100 KG"), "price"] /= 100
    w["unit"] = w["unit"].replace({"100 KG": "KG"})
    keep = ["Rice (coarse)", "Wheat flour", "Oil (palm)", "Oil (soybean, fortified)", "Sugar", "Lentils (masur)", "Eggs (brown)"]
    w = w[w["commodity"].isin(keep)]
    w["month"] = w["date"].str.slice(0, 7)
    g = w.groupby(["month", "commodity", "unit"]).agg(price_bdt=("price", "median"), n_markets=("market", "nunique")).reset_index()
    _save(g.round(3), "wfp_bgd_retail_monthly.csv")


def _ghcnd(station: str, out: str, cols: tuple[str, ...]) -> None:
    rows: dict[tuple[int, int, int], dict] = {}
    for line in (RAW / "noaa_ghcnd" / f"{station}.dly").read_text().splitlines():
        elem = line[17:21]
        if elem not in cols:
            continue
        y, m = int(line[11:15]), int(line[15:17])
        if y < 2019:
            continue
        for d in range(1, 32):
            v = int(line[21 + (d - 1) * 8: 26 + (d - 1) * 8])
            if v == -9999:
                continue
            rows.setdefault((y, m, d), {})[elem] = v / 10.0
    recs = []
    for (y, m, d), vals in sorted(rows.items()):
        try:
            dt = pd.Timestamp(year=y, month=m, day=d)
        except ValueError:
            continue
        if "2020-01-01" <= dt.strftime("%Y-%m-%d") <= END:
            recs.append({"date": dt.strftime("%Y-%m-%d"), **{c.lower(): vals.get(c) for c in cols}})
    df = pd.DataFrame(recs).rename(columns={"tmax": "tmax_c", "tmin": "tmin_c", "prcp": "prcp_mm"})
    _save(df, out)


def ons() -> None:
    x = pd.read_excel(RAW / "ons" / "retail_sales_index_ons.xlsx", sheet_name="Table 1 M", header=None)
    hdr = list(x.iloc[7, :])
    col = hdr.index("Predominantly Non-food Stores")
    seen: dict[str, float] = {}
    for _, r in x.iloc[9:, :].iterrows():
        t = str(r.iloc[0])
        if re.match(r"^\d{4} [A-Z][a-z]{2}$", t) and t not in seen:
            try:
                seen[t] = float(r.iloc[col])
            except (TypeError, ValueError):
                pass
    df = pd.DataFrame({"period": list(seen), "rsi_nonfood_volume_2024_100": list(seen.values())})
    df["month"] = pd.to_datetime(df["period"], format="%Y %b").dt.strftime("%Y-%m")
    df = df[df["month"] >= "2019-12"]
    _save(df[["month", "rsi_nonfood_volume_2024_100"]], "ons_retail_nonfood_volume_monthly.csv")
    rows = []
    for line in (RAW / "ons" / "ons_cpi_d7bt.csv").read_text(encoding="utf-8").splitlines():
        m = re.match(r'^"(\d{4}) ([A-Z]{3})","([\d.]+)"$', line)
        if m:
            rows.append({"month": pd.to_datetime(f"{m.group(1)} {m.group(2)}", format="%Y %b").strftime("%Y-%m"), "cpi_2015_100": float(m.group(3))})
    df = pd.DataFrame(rows)
    _save(df[df["month"] >= "2019-12"], "ons_cpi_d7bt_monthly.csv")


def boe() -> None:
    h = (RAW / "boe" / "bank_rate.html").read_text(encoding="utf-8", errors="ignore")
    rows = re.findall(r'<td align="left">([^<]+)</td>\s*<td align="right">\s*([\d.]+)\s*</td>', h)
    recs = [{"date": pd.to_datetime(d.strip(), format="%d %b %y").strftime("%Y-%m-%d"), "bank_rate_pct": float(r)} for d, r in rows]
    df = pd.DataFrame(recs).sort_values("date")
    _save(df[df["date"] >= "2019-01-01"], "boe_bank_rate_changes.csv")


def basket() -> None:
    """Shape statistics only from UCI Online Retail II (CC BY 4.0). No rows are copied."""
    zp = RAW / "uci_online_retail2" / "online_retail_ii.zip"
    with zipfile.ZipFile(zp) as z:
        name = [n for n in z.namelist() if n.lower().endswith(".xlsx")][0]
        data = io.BytesIO(z.read(name))
    frames = [pd.read_excel(data, sheet_name=s, usecols=["Invoice", "Quantity", "InvoiceDate", "Price"]) for s in ("Year 2009-2010", "Year 2010-2011")]
    d = pd.concat(frames)
    d = d[~d["Invoice"].astype(str).str.startswith("C") & (d["Quantity"] > 0) & (d["Price"] > 0)]
    d["val"] = d["Quantity"] * d["Price"]
    per = d.groupby("Invoice").agg(lines=("val", "size"), val=("val", "sum"), ts=("InvoiceDate", "first"))
    hist = per["lines"].clip(upper=15).value_counts(normalize=True).sort_index()
    out = [{"stat": "lines_per_order_share", "key": str(int(k)), "value": round(float(v), 5)} for k, v in hist.items()]
    wd = per["ts"].dt.dayofweek.value_counts(normalize=True).sort_index()
    out += [{"stat": "weekday_share_mon0", "key": str(int(k)), "value": round(float(v), 5)} for k, v in wd.items()]
    hr = per["ts"].dt.hour.value_counts(normalize=True).sort_index()
    out += [{"stat": "hour_share", "key": str(int(k)), "value": round(float(v), 5)} for k, v in hr.items()]
    q = (per["val"] / per["val"].median()).quantile([.05, .25, .5, .75, .95])
    out += [{"stat": "order_value_over_median_quantile", "key": str(k), "value": round(float(v), 4)} for k, v in q.items()]
    _save(pd.DataFrame(out), "online_retail_ii_basket_shape.csv")


def main() -> None:
    print("building anchors from", RAW)
    fx_daily(); bdt_usd_monthly(); gscpi(); pinksheet(); wfp()
    _ghcnd("UKE00105903", "ghcnd_bradford_daily.csv", ("TMAX", "TMIN", "PRCP"))
    _ghcnd("BGM00041923", "ghcnd_dhaka_daily.csv", ("TMAX", "TMIN", "PRCP"))
    ons(); boe()
    if "--basket" in sys.argv:
        basket()


if __name__ == "__main__":
    main()
