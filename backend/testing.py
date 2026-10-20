"""Test helpers shared by every agent's tests (in-memory DB, mock LLM, no network).

    from backend.testing import make_container, make_client
    ct = make_container()                    # dev fixture loaded, company 'devco' at tier T2
    client = make_client(ct)                 # FastAPI TestClient bound to that container
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from backend.agent.providers.base import LLMProvider
from backend.agent.providers.mock import MockProvider
from backend.api.container import Container, build_container
from backend.config import Settings
from backend.connectors.base import ConnectorRegistry
from backend.ledger.db import connect


def make_settings(**overrides: Any) -> Settings:
    # Tests use the fictional devco fixture only; demo companies and private config are opt-in per test.
    base = dict(app_env="dev", llm_provider="mock", llm_model="mock-1", duckdb_path=":memory:",
                seed_dev_fixture=True, seed_demo_companies=False, companies_file="", connector_plugin_dirs=[],
                default_company_tier="T1", llm_daily_usd_cap=1.0, llm_request_usd_cap=0.10)
    base.update(overrides)
    return Settings(**base)


def make_container(provider: LLMProvider | None = None, connectors: ConnectorRegistry | None = None,
                   engine_factory=None, **settings_overrides: Any) -> Container:
    settings = make_settings(**settings_overrides)
    return build_container(settings, db=connect(":memory:"), provider=provider or MockProvider(),
                           connectors=connectors or ConnectorRegistry(), engine_factory=engine_factory)


def make_client(container: Container):
    from fastapi.testclient import TestClient

    from backend.api.main import create_app

    return TestClient(create_app(container=container))


def parse_sse(text: str) -> list[dict]:
    """Parse an SSE body into a list of event dicts."""
    import json

    events = []
    for block in text.strip().split("\n\n"):
        data = [line[len("data: "):] for line in block.splitlines() if line.startswith("data: ")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


def lineage_frame(rows: list[dict], source_system: str = "test", source_file: str = "test.csv") -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["source_system"] = source_system
    df["source_file"] = source_file
    if "source_row_id" not in df.columns:
        df["source_row_id"] = [str(i + 1) for i in range(len(df))]
    return df
