from backend.connectors.base import (
    ConnectionTestResult, Connector, ConnectorMetadata, ConnectorRegistry, DraftWriteResult, PullResult,
)
from backend.engine.interfaces import BaseEngine
from backend.engine.types import AlertList
from backend.testing import make_client, make_container


def test_health_and_companies():
    with make_client(make_container()) as client:
        h = client.get("/health").json()
        assert h["status"] == "ok" and h["db"] == "ok" and h["provider"] == "mock"
        comps = client.get("/companies").json()
        assert comps[0]["id"] == "devco" and comps[0]["granted_tier"] == "T2"


def test_report_endpoints_return_501_until_engine_exists():
    with make_client(make_container()) as client:
        for path in ["/reports/devco/cash-truth", "/reports/devco/pnl?month=2026-09", "/reports/devco/kpis",
                     "/reports/devco/forecast", "/reports/devco/alerts", "/reports/devco/financing"]:
            r = client.get(path)
            assert r.status_code == 501, path
            assert r.json()["code"] == "not_implemented"
        r = client.post("/reports/devco/scenario", json={"changes": [{"kind": "revenue_pct", "value": -10}]})
        assert r.status_code == 501
        assert client.get("/reports/nope/kpis").status_code == 404


def test_report_endpoint_uses_engine_implementation_when_present():
    class Engine(BaseEngine):
        def alerts(self, company_id, as_of):
            return AlertList(company_id=company_id, as_of=as_of, alerts=[], summary="No alerts.")

    with make_client(make_container(engine_factory=Engine)) as client:
        r = client.get("/reports/devco/alerts?as_of=2026-10-01")
    assert r.status_code == 200 and r.json()["summary"] == "No alerts."


def test_connector_registry_endpoint():
    reg = ConnectorRegistry()

    @reg.register
    class Fake(Connector):
        metadata = ConnectorMetadata(id="fake_csv", name="Fake CSV", category="file", auth_type="file_upload",
                                     status="live", canonical_tables=["external_statement_lines"])

        def test_connection(self):
            return ConnectionTestResult(ok=True, message="ok")

        def discover(self):
            return []

        def pull(self, since=None):
            return PullResult(frames={})

    reg.register_planned(ConnectorMetadata(id="xero", name="Xero", category="accounting", auth_type="oauth2",
                                           status="planned"))
    with make_client(make_container(connectors=reg)) as client:
        data = client.get("/connectors").json()
    assert [d["id"] for d in data] == ["fake_csv", "xero"]
    assert data[1]["status"] == "planned"


def test_owner_token_protects_decisions_and_permissions():
    ct = make_container(owner_api_token="s3cret-test-token")
    with make_client(ct) as client:
        assert client.put("/companies/devco/permissions", json={"tier": "T3"}).status_code == 401
        ok = client.put("/companies/devco/permissions", json={"tier": "T3"},
                        headers={"Authorization": "Bearer s3cret-test-token", "X-Actor": "owner:test"})
        assert ok.status_code == 200 and ok.json()["granted_tier"] == "T3"
        assert client.post("/approvals/apr-x/approve").status_code == 401
    assert ct.audit.list(action="owner_auth_failed")


def test_evidence_endpoint_and_tools_listing():
    with make_client(make_container()) as client:
        ev = client.get("/evidence", params={"company_id": "devco", "ref": ["accounts:devco-acc-1000"]}).json()
        assert ev["rows"][0]["row"]["name"] == "Cash in hand"
        tools = {t["name"]: t for t in client.get("/tools?company_id=devco").json()}
        assert tools["draft_journal_entry"]["tier"] == "T2" and tools["draft_journal_entry"]["allowed"] is True
        assert tools["mark_reminder_ready"]["allowed"] is False
        assert client.get("/usage").json()["spent_usd"] == 0.0


def test_cors_allows_frontend_origin():
    with make_client(make_container()) as client:
        r = client.options("/health", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
