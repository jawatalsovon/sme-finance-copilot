"""Canonical JSON, hashing, money formatting and time helpers."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if hasattr(o, "item"):  # numpy scalars
        return o.item()
    return str(o)


def _clean(o: Any) -> Any:
    """NaN/inf are not valid JSON - turn them into null."""
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def canonical_json(obj: Any) -> str:
    """Stable JSON (sorted keys, no spaces) used for hashing."""
    return json.dumps(_clean(obj), sort_keys=True, separators=(",", ":"), default=_default, ensure_ascii=False)


def to_json(obj: Any) -> str:
    return json.dumps(_clean(obj), default=_default, ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def business_today() -> dt.date:
    """'Today' for business logic (as_of defaults, last month ...). COPILOT_TODAY=YYYY-MM-DD
    pins it so scripted runs are reproducible on any day. Cost caps always use the real UTC day."""
    pinned = os.environ.get("COPILOT_TODAY", "").strip()
    return dt.date.fromisoformat(pinned) if pinned else utc_now().date()


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="microseconds")


def to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def format_money(amount: Any, currency: str) -> str:
    """Deterministic money text for summaries, e.g. 'BDT 1,234,567.00' or 'BDT -2,500.00'."""
    d = to_decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{currency} {d:,.2f}"
