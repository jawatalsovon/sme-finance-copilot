"""Private / self-hosted instance: companies, tiers, connectors and DB path come from config."""
import json
from pathlib import Path

import pytest

from backend.agent.providers import MockProvider
from backend.api.container import build_container
from backend.config import REPO_ROOT, Settings
from backend.connectors.base import registry as global_registry
from backend.instance import InstanceConfigError, read_companies_file
from backend.ledger import connect
from backend.testing import make_settings

COMPANIES = [
    {"id": "acme_private", "name": "Example Private Ltd", "country": "GB", "base_currency": "GBP",
     "industry": "services", "granted_tier": "T2"},
    {"id": "acme_branch", "name": "Example Branch", "country": "BD", "base_currency": "BDT"},
]

PLUGIN = '''
from backend.connectors import Connector, ConnectorMetadata, ConnectionTestResult, PullResult, register

@register
class PrivateLedgerExport(Connector):
    metadata = ConnectorMetadata(id="test_private_plugin", name="Private ledger export", category="file",
                                 auth_type="file_upload", status="live")
    def test_connection(self): return ConnectionTestResult(ok=True, message="ok")
    def discover(self): return []
    def pull(self, since=None): return PullResult(frames={})
'''


@pytest.fixture
def private_dir(tmp_path):
    (tmp_path / "companies.json").write_text(json.dumps(COMPANIES), encoding="utf-8")
    (tmp_path / "connectors").mkdir()
    (tmp_path / "connectors" / "private_ledger.py").write_text(PLUGIN, encoding="utf-8")
    (tmp_path / "connectors" / "broken.py").write_text("raise RuntimeError('boom')", encoding="utf-8")
    return tmp_path


def test_settings_resolve_everything_from_the_private_dir(monkeypatch, private_dir):
    monkeypatch.setenv("COPILOT_PRIVATE_DIR", str(private_dir))
    for var in ("DUCKDB_PATH", "COMPANIES_FILE", "CONNECTOR_PLUGIN_DIRS"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.from_env()
    assert Path(s.resolved_duckdb_path()) == private_dir / "copilot.duckdb"        # per-instance DB
    assert Path(s.companies_file) == private_dir / "companies.json"
    assert [Path(p) for p in s.connector_plugin_dirs] == [private_dir / "connectors"]
    monkeypatch.setenv("DUCKDB_PATH", "db/other.duckdb")
    assert Path(Settings.from_env().resolved_duckdb_path()) == private_dir / "db" / "other.duckdb"


def test_defaults_without_private_dir(monkeypatch):
    for var in ("COPILOT_PRIVATE_DIR", "DUCKDB_PATH", "SEED_DEV_FIXTURE", "SEED_DEMO_COMPANIES", "APP_ENV",
                "DEMO_MODE"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.from_env()
    assert s.seed_dev_fixture is False and s.seed_demo_companies is True and s.app_env == "dev"
    assert Path(s.duckdb_path) == Path("data/copilot.duckdb") and s.companies_file == ""


def test_private_instance_container_loads_companies_tiers_and_plugins(private_dir):
    settings = make_settings(private_dir=str(private_dir), seed_dev_fixture=False, seed_demo_companies=False,
                             companies_file=str(private_dir / "companies.json"),
                             connector_plugin_dirs=[str(private_dir / "connectors")])
    try:
        ct = build_container(settings, db=connect(":memory:"), provider=MockProvider())
        ids = [r["id"] for r in ct.db.query_dicts("SELECT id FROM companies ORDER BY id")]
        assert ids == ["acme_branch", "acme_private"]                  # no demo or dev companies
        assert ct.permissions.get_tier("acme_private").value == "T2"    # tier from config
        assert ct.permissions.get_tier("acme_branch").value == "T1"     # DEFAULT_COMPANY_TIER
        assert "test_private_plugin" in [m.id for m in ct.connectors.list_metadata()]
        plug = json.loads(ct.audit.list(action="connector_plugins_loaded")[0]["payload_json"])
        assert plug["loaded"] == ["private_ledger.py"] and plug["errors"][0]["path"] == "broken.py"
        # an owner decision survives a restart: config tier is only the initial grant
        ct.permissions.set_tier("acme_private", "T1", actor="owner")
        from backend.api.container import seed_state
        seed_state(ct)
        assert ct.permissions.get_tier("acme_private").value == "T1"
    finally:
        global_registry._classes.pop("test_private_plugin", None)


def test_companies_file_is_validated(tmp_path):
    bad = tmp_path / "companies.json"
    bad.write_text(json.dumps([{"id": "has space", "name": "x", "country": "GB", "base_currency": "GBP"}]))
    with pytest.raises(InstanceConfigError):
        read_companies_file(bad)
    bad.write_text(json.dumps([{"id": "ok", "name": "x", "country": "GB", "base_currency": "GBP", "tier": "T9"}]))
    with pytest.raises(InstanceConfigError):
        read_companies_file(bad)


def test_private_dir_inside_repo_must_be_git_ignored():
    from backend.instance import private_dir_warning

    assert private_dir_warning(make_settings(private_dir=str(REPO_ROOT / "backend"))) is not None
    assert private_dir_warning(make_settings(private_dir=str(REPO_ROOT / "data" / "private"))) is None
    assert private_dir_warning(make_settings(private_dir=str(REPO_ROOT.parent / "elsewhere"))) is None


def test_no_real_company_names_in_repo_config():
    demo = json.loads((REPO_ROOT / "backend" / "ledger" / "demo_companies.json").read_text(encoding="utf-8"))
    assert all(c["description"].startswith("Fictional demo SME") for c in demo)
