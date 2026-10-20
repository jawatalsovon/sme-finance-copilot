from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SYN = ROOT / "data" / "synthetic"
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean_connector_env(monkeypatch, tmp_path):
    """No real credentials can leak into a test; token/profile files go to tmp."""
    for name in ["QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REALM_ID", "QBO_REFRESH_TOKEN", "QBO_ENV", "SQUARE_ACCESS_TOKEN",
                 "SQUARE_ENVIRONMENT", "SQUARE_LOCATION_ID", "GOOGLE_SERVICE_ACCOUNT_FILE"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("COPILOT_PRIVATE_DIR", str(tmp_path / "private"))


def need_synthetic(company: str) -> Path:
    root = SYN / company
    if not (root / "answer_key.json").exists():
        pytest.skip(f"synthetic data for {company} missing: python scripts/data/generate.py --seed 42")
    return root
