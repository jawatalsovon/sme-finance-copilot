"""Identifier helpers.

Row ids must match ID_PATTERN so that an evidence reference "table:id" is unambiguous.
"""
from __future__ import annotations

import hashlib
import random
import re
import threading
import uuid

ID_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]+$")
_SAFE = re.compile(r"[^A-Za-z0-9_.\-]+")


def make_id(prefix: str, *parts: object, length: int = 16) -> str:
    """Deterministic id from its parts, e.g. make_id("jl", "bank_csv", "file.csv", 17).

    Same inputs -> same id, so re-ingesting a file is idempotent.
    """
    raw = "\x1f".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]
    return f"{safe_token(prefix)}-{digest}"


_rng: random.Random | None = None
_rng_lock = threading.Lock()


def reseed(seed: object | None) -> None:
    """Deterministic mode for scripted runs: with a seed, new_id() draws from a seeded
    generator (process-wide), so the same seed + same starting state gives the same ids.
    reseed(None) returns to random uuid4 ids."""
    global _rng
    with _rng_lock:
        _rng = None if seed is None else random.Random(str(seed))


def new_id(prefix: str) -> str:
    """Id for runtime objects (approvals, sessions, usage rows, batches)."""
    with _rng_lock:
        token = f"{_rng.getrandbits(64):016x}" if _rng is not None else uuid.uuid4().hex[:16]
    return f"{safe_token(prefix)}-{token}"


def safe_token(text: str) -> str:
    return _SAFE.sub("_", str(text)).strip("_") or "x"


def is_valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(ID_PATTERN.match(value))
