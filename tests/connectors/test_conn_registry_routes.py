"""Registry (GET /connectors) and the connector HTTP routes."""
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.connectors._routes import build_router
from backend.connectors.base import registry
from backend.testing import make_client, make_container
from tests.connectors.test_conn_statements import BANK_WD

registry.autodiscover()
SIX = {"csv_upload", "gsheets", "qbo", "bank_csv", "wallet_csv", "pos"}


def test_get_connectors_lists_exactly_the_six_with_honest_status():
    with make_client(make_container(connectors=registry)) as client:
        data = client.get("/connectors").json()
    by_id = {d["id"]: d for d in data}
    assert set(by_id) == SIX and len(data) == 6                      # no fake 'planned' rows
    assert all(d["status"] != "planned" for d in data)
    assert by_id["qbo"]["status"] == "sandbox" and by_id["pos"]["status"] == "sandbox"
    assert {by_id[i]["status"] for i in ("csv_upload", "bank_csv", "wallet_csv", "gsheets")} == {"file"}
    assert by_id["qbo"]["supports_write_draft"] is True
    assert not [i for i in SIX - {"qbo"} if by_id[i]["supports_write_draft"]]
    for d in data:                                                    # every row says which tables it fills and what it needs
        assert d["canonical_tables"] and d["description"] and d["modes"]
    assert set(by_id["qbo"]["env_vars"]) >= {"QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REALM_ID", "QBO_REFRESH_TOKEN"}
    assert "SQUARE_ACCESS_TOKEN" in by_id["pos"]["env_vars"]


def test_every_registered_connector_reports_not_configured_cleanly_without_credentials():
    for cid in sorted(SIX):
        res = registry.create(cid, "co", {}).test_connection()
        assert res.ok is False and "not configured" in res.message, cid


def test_no_connector_exposes_secrets_in_metadata():
    for m in registry.list_metadata():
        text = m.model_dump_json().lower()
        assert "secret_value" not in text and "token_value" not in text


@pytest.fixture()
def client(tmp_path, monkeypatch):
    ct = make_container(connectors=registry)
    app = FastAPI()
    app.include_router(build_router(lambda request: ct, lambda: None, lambda: "owner"))
    up = tmp_path / "private" / "uploads"
    up.mkdir(parents=True)
    (up / "m.csv").write_text(BANK_WD, encoding="utf-8")
    c = TestClient(app)
    c.ct, c.up = ct, up
    return c


def test_paths_outside_data_or_private_dir_are_refused(client):
    r = client.post("/connectors/bank_csv/test", json={"config": {"path": "C:/Windows/win.ini"}})
    assert r.status_code == 400
    r = client.post("/connectors/bank_csv/test", json={"config": {"path": "../../etc/passwd"}})
    assert r.status_code == 400


def test_api_callers_cannot_inject_a_client_or_token(client):
    r = client.post("/connectors/pos/test", json={"config": {"mode": "square_api", "access_token": "x", "base_url": "http://evil"}})
    assert r.status_code == 200 and r.json()["ok"] is False and "not configured" in r.json()["message"]


def test_propose_then_confirm_then_ingest(client):
    path = str(client.up / "m.csv")
    prop = client.post("/connectors/csv/propose", json={"path": path, "connector": "bank_csv"}).json()
    assert prop["columns"]["debit"] == "Withdrawal" or prop["source"].startswith("profile:")
    r = client.post("/connectors/bank_csv/ingest", json={"company_id": "devco", "config": {"path": path, "base_currency": "USD"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tables"]["external_statement_lines"]["inserted"] == 3 and body["tables"]["external_statement_lines"]["rejected"] == 0
    again = client.post("/connectors/bank_csv/ingest", json={"company_id": "devco", "config": {"path": path, "base_currency": "USD"}}).json()
    assert again["tables"]["external_statement_lines"]["inserted"] == 0 and again["tables"]["external_statement_lines"]["skipped_existing"] == 3
    n = client.ct.db.scalar("SELECT count(*) FROM audit_log WHERE action = 'connector_ingest'")
    assert n == 2
    assert client.ct.db.scalar("SELECT count(*) FROM external_statement_lines WHERE source_system = 'bank_csv'") == 3


def test_qbo_authorize_needs_credentials_and_callback_checks_state(client, monkeypatch):
    assert client.get("/connectors/qbo/authorize").status_code == 400
    monkeypatch.setenv("QBO_CLIENT_ID", "cid")
    monkeypatch.setenv("QBO_CLIENT_SECRET", "sec")
    r = client.get("/connectors/qbo/authorize")
    assert r.status_code == 200 and "appcenter.intuit.com" in r.json()["url"] and "sec" not in r.text
    assert client.get("/connectors/qbo/callback", params={"code": "c", "state": "forged"}).status_code == 400


def test_unknown_connector_is_404(client):
    assert client.post("/connectors/nope/test", json={"config": {}}).status_code == 404
