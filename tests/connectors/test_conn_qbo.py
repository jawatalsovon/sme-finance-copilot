"""QuickBooks Online sandbox connector on recorded-style fixtures: no network, no credentials."""
import json

import httpx
import pandas as pd
import pytest

from backend.connectors._qbo_fixture import FixtureTransport
from backend.connectors.base import ConnectorError, registry
from backend.ledger.db import connect
from backend.ledger.loader import load_frames
from tests.connectors.conftest import FIX

registry.autodiscover()
QBO_FIX = FIX / "qbo"


def make(tmp_path, **cfg):
    return registry.create("qbo", "democo", {"fixtures_dir": str(QBO_FIX), "token_file": str(tmp_path / "tok.json"),
                                             "idempotency_file": str(tmp_path / "keys.json"), **cfg})


def test_not_configured_status_is_clean_and_names_the_variables():
    c = registry.create("qbo", "democo", {})
    r = c.test_connection()
    assert r.ok is False and "not configured" in r.message
    for name in ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REALM_ID", "QBO_REFRESH_TOKEN"):
        assert name in r.message
    with pytest.raises(ConnectorError, match="not configured"):
        c.pull()
    with pytest.raises(ConnectorError, match="not configured"):
        c.write_draft({"lines": []})


def test_production_is_refused(monkeypatch):
    for k, v in {"QBO_CLIENT_ID": "x", "QBO_CLIENT_SECRET": "y", "QBO_REALM_ID": "1", "QBO_REFRESH_TOKEN": "z", "QBO_ENV": "production"}.items():
        monkeypatch.setenv(k, v)
    c = registry.create("qbo", "democo", {})
    assert "sandbox" in c.test_connection().message


def test_live_mode_activates_only_when_credentials_exist(monkeypatch):
    c = registry.create("qbo", "democo", {})
    assert not c.test_connection().ok
    for k, v in {"QBO_CLIENT_ID": "x", "QBO_CLIENT_SECRET": "y", "QBO_REALM_ID": "1", "QBO_REFRESH_TOKEN": "z"}.items():
        monkeypatch.setenv(k, v)
    calls = []

    def handler(req):
        calls.append(req.url.host)
        return httpx.Response(401, json={})

    c2 = registry.create("qbo", "democo", {"http_client": httpx.Client(transport=httpx.MockTransport(handler))})
    assert c2.test_connection().ok is False and calls == ["oauth.platform.intuit.com"]      # it tried the real token endpoint


def test_pull_maps_documents_to_balanced_journal_entries(tmp_path):
    c = make(tmp_path)
    assert c.test_connection().ok
    res = c.pull()
    fr = res.frames
    assert {k: len(v) for k, v in fr.items()} == {"accounts": 13, "counterparties": 4, "journal_entries": 8, "journal_lines": 20,
                                                  "invoices": 2, "bills": 1, "invoice_payments": 1, "bill_payments": 1}
    jl = fr["journal_lines"]
    assert pd.to_numeric(jl["amount_base"]).groupby(jl["entry_id"]).sum().abs().max() == 0
    inv = fr["invoices"].set_index("invoice_no")
    assert inv.loc["1001", "status"] == "paid" and inv.loc["1002", "status"] == "open" and inv.loc["1002", "tax"] == "40.00"
    bill = fr["bills"].iloc[0]
    assert bill["status"] == "partially_paid" and bill["expense_account_id"] == "democo-qbo77"
    # inventory item bought on a bill goes to the Inventory Asset account, not to COGS
    je = fr["journal_entries"].set_index("source_file")
    bl = jl[jl["entry_id"] == je.loc["qbo:Bill", "id"]].set_index("account_id")
    assert bl.loc["democo-qbo81", "debit"] == "450.00" and bl.loc["democo-qbo49", "credit"] == "750.00"
    assert fr["invoice_payments"].iloc[0]["amount"] == "1080.00"
    assert set(jl["source_system"]) == {"quickbooks"} and all(str(x).startswith("qbo:") for x in jl["source_file"])
    assert any("not mapped yet" in w for w in res.warnings)
    reports = load_frames(connect(":memory:"), fr, company_id="democo", if_exists="skip")
    assert all(r.rejected_count == 0 for r in reports.values())


def test_oauth_refresh_rotates_and_stores_the_new_refresh_token(tmp_path):
    c = make(tmp_path)
    c.pull()
    saved = json.loads((tmp_path / "tok.json").read_text())
    assert saved["refresh_token"] == "rt-new"
    assert "at-fixture" not in (tmp_path / "tok.json").read_text()                  # access tokens are never persisted


def test_write_draft_creates_one_sandbox_journal_entry_and_is_idempotent(tmp_path):
    c = make(tmp_path)
    payload = {"date": "2026-10-19", "memo": "Accrue rent", "approval_id": "apr-123",
               "lines": [{"account": "Rent or Lease", "debit": "500.00"}, {"account": "democo-qbo49", "credit": "500.00"}]}
    r1 = c.write_draft(payload)
    assert r1.ok and r1.sandbox and r1.external_id == "9001"
    transport = c._http()._transport
    body = transport.created_bodies[0]
    assert body["PrivateNote"].startswith("[DRAFT by SME Finance Copilot - approval apr-123]")
    assert [l["JournalEntryLineDetail"]["PostingType"] for l in body["Line"]] == ["Debit", "Credit"]
    assert [l["JournalEntryLineDetail"]["AccountRef"]["value"] for l in body["Line"]] == ["62", "49"]
    # same approval again (even from a new connector instance): no second entry, same id back
    c2 = make(tmp_path)
    r2 = c2.write_draft(payload)
    assert r2.ok and r2.external_id == "9001" and "idempotent" in r2.message
    assert c2._http()._transport.created_bodies == []
    # the requestid sent to Intuit is the same key as the local one
    posts = [x for x in transport.calls if x[0] == "POST" and x[1].endswith("/journalentry")]
    assert len(posts) == 1 and len(posts[0][2]["params"]["requestid"]) == 40


def test_write_draft_rejects_bad_entries_without_calling_quickbooks(tmp_path):
    c = make(tmp_path)
    unbalanced = c.write_draft({"lines": [{"account": "62", "debit": "10"}, {"account": "49", "credit": "9"}]})
    assert not unbalanced.ok and "does not balance" in unbalanced.message
    single = c.write_draft({"lines": [{"account": "62", "debit": "10"}]})
    assert not single.ok
    unknown = c.write_draft({"lines": [{"account": "No such account", "debit": "10"}, {"account": "49", "credit": "10"}]})
    assert not unknown.ok and "unknown account" in unknown.message
    assert not any(m == "POST" and p.endswith("/journalentry") for m, p, _ in c._http()._transport.calls)


def test_metadata_is_honest():
    meta = registry.get_class("qbo").metadata
    assert meta.status == "sandbox" and meta.supports_write_draft and "QBO_REFRESH_TOKEN" in meta.env_vars


def test_authorization_code_flow_builds_url_and_stores_tokens(tmp_path):
    from urllib.parse import parse_qs, urlparse

    from backend.connectors._qbo_client import QboClient, QboConfig

    cfg = QboConfig({"client_id": "cid", "client_secret": "sec", "realm_id": "", "token_file": tmp_path / "t.json",
                     "redirect_uri": "http://localhost:8000/connectors/qbo/callback"})
    url, state = QboClient.authorize_url(cfg)
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["cid"] and q["response_type"] == ["code"] and q["state"] == [state]
    assert q["scope"] == ["com.intuit.quickbooks.accounting"] and q["redirect_uri"] == [cfg.redirect_uri]
    assert "sec" not in url                                                            # the secret never goes in the URL
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = req.content.decode()
        return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt-first", "expires_in": 3600})

    QboClient(cfg, httpx.Client(transport=httpx.MockTransport(handler))).exchange_code("auth-code-1", "realm-77")
    assert seen["auth"].startswith("Basic ") and "grant_type=authorization_code" in seen["body"] and "auth-code-1" in seen["body"]
    saved = json.loads((tmp_path / "t.json").read_text())
    assert saved["refresh_token"] == "rt-first" and saved["realm_id"] == "realm-77"
