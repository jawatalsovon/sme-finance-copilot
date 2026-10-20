"""Load the BOOKS view of the three demo SMEs into the ledger (what their systems contain, not the truth).

    from scripts.data.seed import seed
    counts = seed(db)          # {table: rows inserted} summed over the three companies

Reads data/synthetic/<company>/books/*.csv[.gz]. Reality tables (bank/wallet statement lines, cash counts) and
POS orders come from truth/ because they are what the outside world / the POS really recorded.
Run `python scripts/data/generate.py` first. Never loads the derived view pos_sales_daily.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SYN = ROOT / "data" / "synthetic"
COMPANIES = ("bd_grocer", "cn_xborder", "uk_garden")
FROM_TRUTH = {"external_statement_lines", "pos_orders", "pos_order_lines", "pos_payments"}


def _read(folder: Path, table: str) -> pd.DataFrame | None:
    for name in (f"{table}.csv", f"{table}.csv.gz"):
        p = folder / name
        if p.exists():
            return pd.read_csv(p, dtype=str, keep_default_na=False, na_values=[""])
    return None


def seed(db) -> dict:
    from backend.ledger.db import DATA_TABLES
    from backend.ledger.loader import load_dataframe

    counts: dict[str, int] = {t: 0 for t in DATA_TABLES}
    rejected = 0
    for cid in COMPANIES:
        root = SYN / cid
        if not (root / "answer_key.json").exists():
            raise FileNotFoundError(f"{root} missing: run `python scripts/data/generate.py` first")
        for table in DATA_TABLES:
            folder = root / ("truth" if table in FROM_TRUTH else "books")
            df = _read(folder, table)
            if df is None or df.empty:
                continue
            rep = load_dataframe(db, table, df, if_exists="skip")
            counts[table] += rep.inserted
            rejected += rep.rejected_count
    counts["_rejected"] = rejected
    return counts
