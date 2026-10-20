"""Cash-flow forecast: 13 weeks or 12 months, with 80% and 95% intervals and an honest backtest.

Plain words:
  * We forecast money IN and money OUT separately (both from the corrected cash history), then
    closing cash = opening verified cash + cumulative (in - out).
  * Holidays that move every year (Eid, Chinese New Year, Easter ...) are taken out of the history
    as a multiplicative factor ("Eid weeks bring in 1.4x a normal week"), the series is forecast,
    and the factor is put back on the future weeks that contain the holiday.
  * Three simple, explainable models are averaged:
      - seasonal naive: same period last year (scaled to the recent level),
      - Holt-Winters exponential smoothing (level + damped trend + yearly season),
      - Theta: smoothed level plus half the long-run trend, on the de-seasonalised series.
  * Intervals come from our own past mistakes: a rolling-origin backtest (forecast from many past
    dates, compare with what really happened) gives the spread of errors at each horizon; the
    80% / 95% bands are those empirical error quantiles, widening with the horizon.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backend.core.util import format_money
from backend.engine import calendars
from backend.engine.data import CASH_SUBTYPES, Ledger, dec
from backend.engine.interfaces import EngineError
from backend.engine.types import BacktestResult, CashflowForecast, Figure, ForecastPoint

METHODS = ("seasonal_naive", "holt_winters", "theta")


# ---------------------------------------------------------------------------- series
def cash_flow_series(L: Ledger, corrections: pd.DataFrame | None, as_of: dt.date, freq: str) -> pd.DataFrame:
    """Periods (W-SUN weeks or months) with columns inflow, outflow (base units, float, >= 0)."""
    lines = L.lines_upto(as_of)
    cols = ["entry_id", "entry_date", "acc_subtype", "base_c"]
    frames = [lines[cols]]
    if corrections is not None and len(corrections):
        c = corrections[corrections["entry_date"] <= as_of]
        frames.append(c[cols])
    df = pd.concat(frames, ignore_index=True)
    is_cash = df["acc_subtype"].isin(CASH_SUBTYPES)
    non_cash_entries = set(df.loc[~is_cash, "entry_id"])
    cash = df[is_cash & df["entry_id"].isin(non_cash_entries)]
    net = cash.groupby(["entry_id", "entry_date"])["base_c"].sum().reset_index()
    if net.empty:
        raise EngineError(f"no cash movements before {as_of} for {L.company_id}")
    net["d"] = pd.to_datetime(net["entry_date"])
    net["inflow"] = net["base_c"].clip(lower=0) / 100.0
    net["outflow"] = (-net["base_c"]).clip(lower=0) / 100.0
    rule = "W-SUN" if freq == "week" else "MS"
    s = net.set_index("d")[["inflow", "outflow"]].resample(rule).sum()
    # drop the incomplete last period
    end = pd.Timestamp(as_of)
    if freq == "week":
        s.index = s.index - pd.Timedelta(days=6)  # label = Monday of the week
        if (s.index[-1] + pd.Timedelta(days=6)) > end:
            s = s.iloc[:-1]
    else:
        if (s.index[-1] + pd.offsets.MonthEnd(0)) > end:
            s = s.iloc[:-1]
    return s


def period_bounds(start: pd.Timestamp, freq: str) -> tuple[dt.date, dt.date]:
    if freq == "week":
        return start.date(), (start + pd.Timedelta(days=6)).date()
    return start.date(), (start + pd.offsets.MonthEnd(0)).date()


# ---------------------------------------------------------------------------- holidays
def holiday_matrix(country: str, index: pd.DatetimeIndex, freq: str) -> pd.DataFrame:
    """Share of each period's days that fall in each holiday window (0..1)."""
    if len(index) == 0:
        return pd.DataFrame(index=index)
    start, end = index[0].date(), period_bounds(index[-1], freq)[1]
    ev = calendars.events(country, start, end)
    names = sorted({n for n, _, _ in ev})
    m = pd.DataFrame(0.0, index=index, columns=names)
    for n, s, e in ev:
        for p in index:
            ps, pe = period_bounds(p, freq)
            ov = (min(pe, e) - max(ps, s)).days + 1
            if ov > 0:
                m.loc[p, n] = min(1.0, m.loc[p, n] + ov / ((pe - ps).days + 1))
    return m


def holiday_factors(y: pd.Series, H: pd.DataFrame, min_events: int = 2) -> dict[str, float]:
    """Multiplicative effect per holiday: median of actual / local baseline in periods it covers."""
    out = {}
    if H.empty or len(y) < 20:
        return out
    any_h = (H.sum(axis=1) > 0)
    base = y.where(~any_h).rolling(9, center=True, min_periods=3).median()
    base = base.fillna(y.rolling(9, center=True, min_periods=1).median())
    for n in H.columns:
        w = H[n]
        mask = (w >= 0.5) & (base > 0)
        if mask.sum() < min_events:
            continue
        ratio = (y[mask] / base[mask]).replace([np.inf, -np.inf], np.nan).dropna()
        if len(ratio) < min_events:
            continue
        f = float(np.clip(np.median(ratio), 0.4, 3.0))
        if abs(f - 1) >= 0.05:
            out[n] = f
    return out


def apply_factors(H: pd.DataFrame, factors: dict[str, float]) -> np.ndarray:
    f = np.ones(len(H))
    for n, v in factors.items():
        if n in H.columns:
            f *= 1 + (v - 1) * H[n].to_numpy()
    return f


# ---------------------------------------------------------------------------- models
def seasonal_naive(y: np.ndarray, h: int, m: int) -> np.ndarray:
    n = len(y)
    if n < m + 4:
        return np.repeat(np.mean(y[-min(n, 8):]), h)
    # same period last year, scaled by the recent level ratio (last 8 periods vs. a year before)
    k = min(8, n - m)
    recent, year_ago = np.mean(y[-k:]), np.mean(y[-m - k:-m])
    scale = recent / year_ago if year_ago > 0 else 1.0
    scale = float(np.clip(scale, 0.5, 2.0))
    idx = [n - m + (i % m) for i in range(h)]
    return np.array([y[i] for i in idx]) * scale


def _hw_run(y, m, alpha, beta, gamma, phi):
    n = len(y)
    l = np.mean(y[:m])
    b = (np.mean(y[m:2 * m]) - np.mean(y[:m])) / m if n >= 2 * m else 0.0
    s = list(y[:m] - l)
    sse = 0.0
    for t in range(m, n):
        st = s[t - m]
        f = l + phi * b + st
        e = y[t] - f
        sse += e * e
        l_new = alpha * (y[t] - st) + (1 - alpha) * (l + phi * b)
        b = beta * (l_new - l) + (1 - beta) * phi * b
        s.append(gamma * (y[t] - l_new) + (1 - gamma) * st)
        l = l_new
    return sse, l, b, s


def holt_winters_fit(y: np.ndarray, m: int) -> tuple[float, float, float, float]:
    best = None
    for a in (0.05, 0.15, 0.3, 0.5):
        for bt in (0.0, 0.05):
            for g in (0.05, 0.15, 0.3):
                sse = _hw_run(y, m, a, bt, g, 0.9)[0]
                if best is None or sse < best[0]:
                    best = (sse, a, bt, g)
    return best[1], best[2], best[3], 0.9


def holt_winters(y: np.ndarray, h: int, m: int, params=None) -> np.ndarray:
    if len(y) < 2 * m + 2:
        return seasonal_naive(y, h, m)
    a, bt, g, phi = params or holt_winters_fit(y, m)
    _, l, b, s = _hw_run(y, m, a, bt, g, phi)
    n = len(y)
    out = []
    damp = 0.0
    for i in range(1, h + 1):
        damp += phi ** i
        out.append(l + damp * b + s[n - m + ((i - 1) % m)])
    return np.maximum(np.array(out), 0.0)


def theta(y: np.ndarray, h: int, m: int) -> np.ndarray:
    """Theta method: de-seasonalise (classical multiplicative indices from up to 3 years), then a
    smoothed level plus half the straight-line trend, then re-seasonalise."""
    n = len(y)
    k = min(n // m, 3) * m
    if k >= 2 * m:
        yy = y[-k:]
        mat = yy.reshape(-1, m)
        lvl = mat.mean(axis=1, keepdims=True)
        idx = np.where(lvl > 0, mat / np.where(lvl == 0, 1, lvl), 1.0).mean(axis=0)
        idx = idx / idx.mean() if idx.mean() > 0 else np.ones(m)
        idx = np.where(idx <= 0.05, 0.05, idx)
    else:
        idx = np.ones(m)
    phase = lambda t: (t - (n - k)) % m  # noqa: E731
    ds = y / idx[phase(np.arange(n))]
    t = np.arange(n)
    slope = np.polyfit(t, ds, 1)[0] if n > 3 else 0.0
    alpha = 0.3
    lvl_s = ds[0]
    for v in ds[1:]:
        lvl_s = alpha * v + (1 - alpha) * lvl_s
    fc = lvl_s + 0.5 * slope * (np.arange(1, h + 1) + (1 - alpha) ** n / alpha - 1 / alpha + 1)
    return np.maximum(fc * idx[phase(np.arange(n, n + h))], 0.0)


def ensemble(y: np.ndarray, h: int, m: int, hw_params=None) -> dict[str, np.ndarray]:
    out = {
        "seasonal_naive": seasonal_naive(y, h, m),
        "holt_winters": holt_winters(y, h, m, hw_params),
        "theta": theta(y, h, m),
    }
    out["ensemble"] = np.mean([out[k] for k in METHODS], axis=0)
    return out


# ---------------------------------------------------------------------------- forecast + backtest
@dataclass
class SeriesModel:
    factors: dict[str, float]
    hw_params: tuple


def _prepare(y: pd.Series, H: pd.DataFrame, m: int) -> tuple[np.ndarray, SeriesModel]:
    fac = holiday_factors(y, H)
    adj = y.to_numpy() / apply_factors(H, fac)
    params = holt_winters_fit(adj, m) if len(adj) >= 2 * m + 2 else None
    return adj, SeriesModel(fac, params)


def predict(y: pd.Series, H_hist: pd.DataFrame, H_fut: pd.DataFrame, h: int, m: int,
            model: SeriesModel | None = None) -> dict[str, np.ndarray]:
    if model is None:
        adj, model = _prepare(y, H_hist, m)
    else:
        adj = y.to_numpy() / apply_factors(H_hist, model.factors)
    fc = ensemble(adj, h, m, model.hw_params)
    f_fut = apply_factors(H_fut, model.factors)
    return {k: v * f_fut for k, v in fc.items()}


def smape(a: np.ndarray, f: np.ndarray) -> float:
    d = np.abs(a) + np.abs(f)
    ok = d > 0
    return float(np.mean(2 * np.abs(a[ok] - f[ok]) / d[ok]) * 100) if ok.any() else 0.0


def backtest(s: pd.DataFrame, country: str, freq: str, h: int, m: int, H_all: pd.DataFrame,
             max_folds: int = 24) -> dict:
    """Rolling-origin backtest. Returns per-method sMAPE, errors of the closing-cash path per horizon."""
    n = len(s)
    min_train = 2 * m + 4
    if n < min_train + h:
        return {"folds": 0}
    step = max(1, (n - min_train - h) // max_folds)
    origins = list(range(min_train, n - h + 1, step))[-max_folds:]
    models = {c: _prepare(s[c].iloc[:origins[0]], H_all.iloc[:origins[0]], m)[1] for c in ("inflow", "outflow")}
    per_method = {k: [] for k in list(METHODS) + ["ensemble"]}
    cum_method = {k: [] for k in list(METHODS) + ["ensemble"]}
    path_err = []      # (h,) relative errors of cumulative net vs. actual, scaled by average flow
    abs_err = []
    for o in origins:
        res = {}
        for c in ("inflow", "outflow"):
            res[c] = predict(s[c].iloc[:o], H_all.iloc[:o], H_all.iloc[o:o + h], h, m, models[c])
        a_in, a_out = s["inflow"].iloc[o:o + h].to_numpy(), s["outflow"].iloc[o:o + h].to_numpy()
        for k in per_method:
            per_method[k].append((smape(a_in, res["inflow"][k]) + smape(a_out, res["outflow"][k])) / 2)
            cum_method[k].append((smape(np.cumsum(a_in), np.cumsum(res["inflow"][k]))
                                  + smape(np.cumsum(a_out), np.cumsum(res["outflow"][k]))) / 2)
        f_net = np.cumsum(res["inflow"]["ensemble"] - res["outflow"]["ensemble"])
        a_net = np.cumsum(a_in - a_out)
        abs_err.append(a_net - f_net)
        scale = max(1.0, float(np.mean(a_in + a_out)))
        path_err.append((a_net - f_net) / scale)
    abs_err = np.array(abs_err)
    return {
        "folds": len(origins),
        "smape": {k: float(np.mean(v)) for k, v in cum_method.items()},          # cumulative in/out per horizon
        "smape_period": {k: float(np.mean(v)) for k, v in per_method.items()},  # each period on its own
        "abs_err": abs_err,
        "first_origin": s.index[origins[0]].date(),
        "last_origin": s.index[origins[-1]].date(),
    }


def cashflow_forecast(L: Ledger, corrections: pd.DataFrame | None, opening: Figure, as_of: dt.date, horizon: int,
                      granularity: str, adjust=None) -> tuple[CashflowForecast, dict]:
    """`adjust(points_df) -> points_df` lets scenarios change the future flows before closing cash is built."""
    if granularity not in ("week", "month"):
        raise EngineError("granularity must be 'week' or 'month'")
    if not 1 <= horizon <= (52 if granularity == "week" else 24):
        raise EngineError("horizon out of range (weeks: 1-52, months: 1-24)")
    m = 52 if granularity == "week" else 12
    s = cash_flow_series(L, corrections, as_of, granularity)
    if len(s) < 8:
        raise EngineError("not enough cash history to forecast (need at least 8 periods)")
    freq_off = pd.Timedelta(weeks=1) if granularity == "week" else pd.offsets.MonthBegin(1)
    fut_index = pd.DatetimeIndex([s.index[-1] + freq_off * (i + 1) for i in range(horizon)]) \
        if granularity == "week" else pd.date_range(s.index[-1] + pd.offsets.MonthBegin(1), periods=horizon, freq="MS")
    H_all = holiday_matrix(L.country, s.index.append(fut_index), granularity)
    H_hist, H_fut = H_all.iloc[:len(s)], H_all.iloc[len(s):]
    fc = {}
    models = {}
    for c in ("inflow", "outflow"):
        adj, models[c] = _prepare(s[c], H_hist, m)
        fc[c] = predict(s[c], H_hist, H_fut, horizon, m, models[c])
    bt = backtest(s, L.country, granularity, horizon, m, H_all.iloc[:len(s)])
    inflow, outflow = fc["inflow"]["ensemble"], fc["outflow"]["ensemble"]
    pts = pd.DataFrame({"start": fut_index, "inflow": inflow, "outflow": outflow})
    if adjust is not None:
        pts = adjust(pts)
    pts["inflow"] = pts["inflow"].round(2)
    pts["outflow"] = pts["outflow"].round(2)
    net = np.round(pts["inflow"].to_numpy() * 100) - np.round(pts["outflow"].to_numpy() * 100)   # cents, exact
    net = net / 100
    open_v = float(opening.value)
    closing = open_v + np.cumsum(net)
    # intervals from backtest path errors; fallback: proportional to volatility
    if bt.get("folds", 0) >= 5:
        e = bt["abs_err"]
        q = {lv: (np.quantile(e, (1 - lv) / 2, axis=0), np.quantile(e, 1 - (1 - lv) / 2, axis=0)) for lv in (0.8, 0.95)}
        coverage = float(np.mean((e >= q[0.8][0]) & (e <= q[0.8][1])))
    else:
        sd = float((s["inflow"] - s["outflow"]).std() or 0) * np.sqrt(np.arange(1, horizon + 1))
        q = {0.8: (-1.2816 * sd, 1.2816 * sd), 0.95: (-1.96 * sd, 1.96 * sd)}
        coverage = None
    points = []
    for i in range(horizon):
        ps, pe = period_bounds(pd.Timestamp(pts["start"].iloc[i]), granularity)
        points.append(ForecastPoint(
            period_start=ps, period_end=pe, inflow=dec(round(pts["inflow"].iloc[i] * 100)),
            outflow=dec(round(pts["outflow"].iloc[i] * 100)),
            net=dec(round(pts["inflow"].iloc[i] * 100)) - dec(round(pts["outflow"].iloc[i] * 100)),
            closing_cash=dec(round(closing[i] * 100)),
            closing_cash_lower=dec(round((closing[i] + q[0.8][0][i]) * 100)),
            closing_cash_upper=dec(round((closing[i] + q[0.8][1][i]) * 100)),
            closing_cash_lower_95=dec(round((closing[i] + q[0.95][0][i]) * 100)),
            closing_cash_upper_95=dec(round((closing[i] + q[0.95][1][i]) * 100))))
    cur = L.base
    unit = "weeks" if granularity == "week" else "months"
    backtest_res = None
    if bt.get("folds", 0):
        sm = bt["smape"]
        best = min(METHODS, key=lambda k: sm[k])
        backtest_res = BacktestResult(
            method="ensemble (seasonal naive + Holt-Winters + Theta) with holiday factors", metric="sMAPE",
            value=round(sm["ensemble"], 2), horizon=horizon, folds=bt["folds"],
            interval_coverage=round(coverage, 3) if coverage is not None else None,
            explanation=(f"Rolling-origin backtest: {bt['folds']} forecasts made from past dates between "
                         f"{bt['first_origin']} and {bt['last_origin']}, each {horizon} {unit} ahead, compared with what "
                         f"really happened. Error measure: sMAPE of cumulative money in and money out at each point of "
                         f"the horizon (what drives closing cash): ensemble {sm['ensemble']:.1f}%, seasonal naive "
                         f"{sm['seasonal_naive']:.1f}%, Holt-Winters {sm['holt_winters']:.1f}%, Theta {sm['theta']:.1f}% "
                         f"(best single model: {best.replace('_', ' ')}). {granularity.capitalize()} by {granularity}, "
                         f"the ensemble's sMAPE is {bt['smape_period']['ensemble']:.1f}% (single {granularity}s are lumpier "
                         f"than totals). "
                         f"The 80% band is built from these past errors."))
    assumptions = [
        f"Opening cash is the verified (statement and cash-count) balance on {as_of}, not the book balance.",
        "History used: cash in and out on the corrected basis (books plus Cash Truth corrections).",
        "Transfers between your own accounts are excluded.",
        "No new loans, asset sales or one-off payments unless a scenario adds them.",
    ]
    for c in ("inflow", "outflow"):
        for n, f in sorted(models[c].factors.items()):
            assumptions.append(f"Holiday effect on money {'in' if c == 'inflow' else 'out'}: {n} periods are "
                               f"{f:.2f}x a normal {granularity}.")
    low = min(range(horizon), key=lambda i: closing[i])
    summary = (f"Starting from verified cash of {format_money(opening.value, cur)}, cash is expected to be "
               f"{format_money(dec(round(closing[-1] * 100)), cur)} after {horizon} {unit} "
               f"(80% range {format_money(points[-1].closing_cash_lower, cur)} to "
               f"{format_money(points[-1].closing_cash_upper, cur)}). Lowest point: "
               f"{format_money(points[low].closing_cash, cur)} in the {granularity} starting {points[low].period_start}.")
    if backtest_res:
        summary += f" Backtest error (sMAPE) {backtest_res.value:.1f}% over {backtest_res.folds} past forecasts."
    result = CashflowForecast(
        company_id=L.company_id, as_of=as_of, horizon=horizon, granularity=granularity,  # type: ignore[arg-type]
        currency=cur, basis="corrected", opening_cash=opening, interval_level=0.8, points=points,
        backtest=backtest_res, method="Average of seasonal naive, Holt-Winters and Theta, with holiday factors",
        assumptions=assumptions, evidence=opening.evidence[:20], summary=summary)
    return result, {"series": s, "models": models, "backtest": bt, "closing": closing, "points_df": pts}


def forecast_series(y: pd.Series, h: int, freq: str, country: str | None = None) -> dict[str, np.ndarray]:
    """The engine's forecast for any one series (weekly 'week' or monthly 'month', DatetimeIndex of period
    starts): holiday factors for `country`, then seasonal naive, Holt-Winters, Theta and their average
    ('ensemble'). Used for validation on public data; same code path as cashflow_forecast."""
    m = 52 if freq == "week" else 12
    step = pd.Timedelta(weeks=1) if freq == "week" else pd.offsets.MonthBegin(1)
    fut = pd.DatetimeIndex([y.index[-1] + step * (i + 1) for i in range(h)]) if freq == "week" else \
        pd.date_range(y.index[-1] + pd.offsets.MonthBegin(1), periods=h, freq="MS")
    H = holiday_matrix(country or "", y.index.append(fut), freq) if country else \
        pd.DataFrame(index=y.index.append(fut))
    return predict(y.astype(float), H.iloc[:len(y)], H.iloc[len(y):], h, m)
