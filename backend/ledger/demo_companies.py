"""Company rows for the three fictional demo SMEs (selection: docs/research/SME_SELECTION.md).

Data lives in demo_companies.json (config, not code) and uses the same format as a private
instance's companies.json. Only the `companies` rows - D1 generates the transactions.
Loaded with if_exists="skip", so D1 can load the same ids again without conflict.
On by default (SEED_DEMO_COMPANIES=1); a private instance sets it to 0.
"""
from __future__ import annotations

from pathlib import Path

from backend.ledger.db import Database

DEMO_COMPANIES_FILE = Path(__file__).resolve().parent / "demo_companies.json"


def demo_companies() -> list[dict]:
    from backend.instance import read_companies_file

    return read_companies_file(DEMO_COMPANIES_FILE)


DEMO_COMPANY_IDS = ("bd_grocer", "cn_xborder", "uk_garden")


def seed_demo_companies(db: Database) -> dict:
    from backend.instance import load_companies

    return load_companies(db, demo_companies(), "backend/ledger/demo_companies.json", source_system="demo_fixture")
