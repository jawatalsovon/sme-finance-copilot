"""Harness API for scripted tasks: /chat/sync, /admin/reset, deterministic seeded runs."""
from backend.testing import make_client, make_container


def test_chat_sync_returns_one_json_object():
    with make_client(make_container()) as client:
        out = client.post("/chat/sync", json={"company_id": "devco", "message": "What is my cash position?"}).json()
    assert out["answer"] and out["error"] is None and out["granted_tier"] == "T2"
    assert out["evidence_ids"] and out["evidence_ids"][0].startswith("journal_lines:")
    assert out["tool_calls"][0]["tool"] == "account_balances" and out["tool_calls"][0]["status"] == "executed"
    assert out["tier_decisions"] == [{"tool": "account_balances", "tool_tier": "T0", "granted_tier": "T2",
                                      "decision": "executed", "reason": "T0 tool allowed at granted tier T2"}]
    assert "events" not in out


def test_chat_sync_can_include_raw_events():
    with make_client(make_container()) as client:
        out = client.post("/chat/sync?include_events=true",
                          json={"company_id": "devco", "message": "hi"}).json()
    assert out["events"][0]["type"] == "session" and out["events"][-1]["type"] == "final"


def test_admin_reset_is_disabled_by_default():
    with make_client(make_container()) as client:
        assert client.post("/admin/reset").status_code == 403


def test_admin_reset_restores_seeded_state_and_restarts_audit_chain():
    ct = make_container(admin_reset_enabled=True, seed_demo_companies=True)
    with make_client(ct) as client:
        client.post("/chat/sync", json={"company_id": "devco", "message": "cash?"})
        ct.executor.execute("devco", "draft_journal_entry", {
            "entry_date": "2026-09-28", "memo": "x", "lines": [{"account_id": "devco-acc-6200", "debit": 1},
                                                               {"account_id": "devco-acc-1010", "credit": 1}]})
        assert client.get("/approvals").json()
        r = client.post("/admin/reset", json={"seed": 7}).json()
        assert r["ok"] and r["companies"] == ["bd_grocer", "cn_xborder", "devco", "uk_garden"]
        assert r["loaded"]["data_hook"]["status"] in ("not_found", "ok")
        assert client.get("/approvals").json() == []
        audit = client.get("/audit?limit=1000").json()
        assert audit[-1]["action"] == "admin_reset" and audit[-1]["seq"] == 1
        assert client.get("/audit/verify").json()["ok"] is True
        assert client.get("/companies/devco/permissions").json()["granted_tier"] == "T2"


def test_same_seed_gives_identical_runs():
    ct = make_container(admin_reset_enabled=True)
    runs = []
    with make_client(ct) as client:
        for _ in range(2):
            client.post("/admin/reset", json={"seed": 42})
            turn1 = client.post("/chat/sync", json={"company_id": "devco", "message": "What is my cash position?",
                                                   "seed": 1}).json()
            turn2 = client.post("/chat/sync", json={"company_id": "devco", "message": "Any late customers?",
                                                   "session_id": turn1["session_id"], "seed": 1}).json()
            runs.append((turn1, turn2))
    assert runs[0] == runs[1]
    assert runs[0][0]["session_id"] == runs[0][1]["session_id"]


def test_pinned_business_today(monkeypatch):
    monkeypatch.setenv("COPILOT_TODAY", "2026-10-20")
    with make_client(make_container()) as client:
        out = client.post("/chat/sync", json={"company_id": "devco", "message": "cash balance?"}).json()
    assert "up to 2026-10-20" in out["answer"]
