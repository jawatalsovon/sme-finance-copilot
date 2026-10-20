"""Owner-token auth: fails closed outside dev; X-Actor is advisory only."""
import json

import pytest

from backend.config import Settings
from backend.testing import make_client, make_container

DRAFT = {"entry_date": "2026-09-28", "memo": "x",
         "lines": [{"account_id": "devco-acc-6200", "debit": 1}, {"account_id": "devco-acc-1010", "credit": 1}]}
TOKEN = "test-owner-token-123"


def owner_calls(client, apr):
    return {
        "approve": client.post(f"/approvals/{apr}/approve"),
        "reject": client.post(f"/approvals/{apr}/reject", json={"note": "n"}),
        "tier": client.put("/companies/devco/permissions", json={"tier": "T3"}),
        "reset": client.post("/admin/reset"),
    }


def failures(ct):
    return [json.loads(r["payload_json"])["reason"] for r in ct.audit.list(action="owner_auth_failed", limit=100)]


@pytest.mark.parametrize("env", ["demo", "prod"])
def test_non_dev_without_configured_token_fails_closed(env):
    ct = make_container(app_env=env, admin_reset_enabled=True)
    apr = ct.executor.execute("devco", "draft_journal_entry", DRAFT).approval_id
    with make_client(ct) as client:
        res = owner_calls(client, apr)
        # even a bearer header and an "owner" X-Actor do not help when no token is configured
        forged = client.post(f"/approvals/{apr}/approve",
                             headers={"Authorization": "Bearer anything", "X-Actor": "owner"})
    assert {k: r.status_code for k, r in res.items()} == {"approve": 401, "reject": 401, "tier": 401, "reset": 401}
    assert forged.status_code == 401 and "not configured" in forged.json()["detail"]
    assert failures(ct) == ["not_configured"] * 5
    assert ct.approvals.get(apr)["status"] == "pending"
    assert ct.permissions.get_tier("devco").value == "T2"


def test_non_dev_missing_or_wrong_token_is_401_and_audited():
    ct = make_container(app_env="demo", owner_api_token=TOKEN, admin_reset_enabled=True)
    apr = ct.executor.execute("devco", "draft_journal_entry", DRAFT).approval_id
    with make_client(ct) as client:
        missing = client.post(f"/approvals/{apr}/approve", headers={"X-Actor": "owner:jawat"})
        wrong = client.post(f"/approvals/{apr}/approve", headers={"Authorization": f"Bearer {TOKEN}x"})
        not_bearer = client.post(f"/approvals/{apr}/approve", headers={"Authorization": TOKEN})
        tier = client.put("/companies/devco/permissions", json={"tier": "T3"},
                          headers={"Authorization": "Bearer nope"})
        assert client.post("/admin/reset").status_code == 401
        ok = client.post(f"/approvals/{apr}/approve", headers={"Authorization": f"Bearer {TOKEN}"})
        ok_tier = client.put("/companies/devco/permissions", json={"tier": "T1"},
                             headers={"Authorization": f"Bearer {TOKEN}"})
    assert [missing.status_code, wrong.status_code, not_bearer.status_code, tier.status_code] == [401] * 4
    assert missing.headers["www-authenticate"] == "Bearer"
    assert sorted(failures(ct)) == ["missing", "missing", "missing", "wrong", "wrong"]
    claimed = [json.loads(r["payload_json"])["claimed_actor"] for r in ct.audit.list(action="owner_auth_failed")]
    assert "owner:jawat" in claimed            # recorded, but it did not authorise anything
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert ok_tier.status_code == 200 and ct.permissions.get_tier("devco").value == "T1"


def test_valid_token_does_not_let_the_agent_approve():
    ct = make_container(app_env="prod", owner_api_token=TOKEN)
    apr = ct.executor.execute("devco", "draft_journal_entry", DRAFT).approval_id
    with make_client(ct) as client:
        r = client.post(f"/approvals/{apr}/approve", headers={"Authorization": f"Bearer {TOKEN}", "X-Actor": "agent"})
    assert r.status_code == 403 and ct.approvals.get(apr)["status"] == "pending"


def test_dev_without_token_stays_open_and_reads_need_no_token():
    ct = make_container(app_env="dev")
    apr = ct.executor.execute("devco", "draft_journal_entry", DRAFT).approval_id
    with make_client(ct) as client:
        assert client.get("/health").json()["owner_auth"] == "off (dev)"
        assert client.post(f"/approvals/{apr}/approve").status_code == 200
    ct2 = make_container(app_env="demo")
    with make_client(ct2) as client:
        assert client.get("/health").json()["owner_auth"] == "required"
        assert client.get("/approvals").status_code == 200 and client.get("/audit/verify").status_code == 200


def test_demo_mode_env_switches_app_env(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.delenv("APP_ENV", raising=False)
    s = Settings.from_env()
    assert s.app_env == "demo" and s.owner_auth_required
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ValueError):
        Settings.from_env()
