"""FX lookup for connectors: units of base currency per 1 unit of a foreign currency, on a date.

Sources, in order: a flat dict {"USD": 7.1} (config 'fx_rates'), a DataFrame / list of
(rate_date, from_currency, to_currency, rate) rows, or a ledger Database with an fx_rates table.
A rate is never invented: `rate()` returns None when nothing is known, and callers must say so.
"""
from __future__ import annotations

import bisect
import datetime as dt
from decimal import Decimal
from typing import Any, Optional

import pandas as pd


class FxResolver:
    def __init__(self, base_currency: str, source: Any = None):
        self.base = base_currency.upper()
        self.flat: dict[str, Decimal] = {}
        self.series: dict[str, tuple[list[dt.date], list[Decimal]]] = {}
        if source is None:
            return
        if isinstance(source, dict):
            self.flat = {k.upper(): Decimal(str(v)) for k, v in source.items()}
        elif isinstance(source, pd.DataFrame):
            self._load_frame(source)
        elif hasattr(source, "query_df"):          # backend.ledger.db.Database
            self._load_frame(source.query_df(
                "SELECT rate_date, from_currency, to_currency, rate FROM fx_rates WHERE to_currency = ?", [self.base]))
        else:
            self._load_frame(pd.DataFrame(list(source)))

    def _load_frame(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        df = df[df["to_currency"].astype(str).str.upper() == self.base]
        for ccy, g in df.groupby(df["from_currency"].astype(str).str.upper()):
            g = g.assign(_d=pd.to_datetime(g["rate_date"]).dt.date).sort_values("_d")
            self.series[ccy] = (g["_d"].tolist(), [Decimal(str(x)) for x in g["rate"]])

    def rate(self, ccy: str, on: Optional[dt.date]) -> Optional[Decimal]:
        ccy = ccy.upper()
        if ccy == self.base:
            return Decimal(1)
        if ccy in self.series and on is not None:
            days, rates = self.series[ccy]
            i = bisect.bisect_right(days, on) - 1
            if i >= 0:
                return rates[i]
            return rates[0]
        return self.flat.get(ccy)
