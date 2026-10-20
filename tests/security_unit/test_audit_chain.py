import json

import pytest

from backend.ledger import connect
from backend.security.audit import AuditLog
from backend.testing import make_client, make_container


@pytest.fixture
def log():
    a = AuditLog(connect(":memory:"))
    for i in range(5):
        a.append("tool_executed", actor="agent", company_id="c1", tool=f"t{i}", tier="T0", payload={"i": i})
    assert a.verify_chain().ok
    return a


def errors(a):
    res = a.verify_chain()
    assert not res.ok
    return " | ".join(e["error"] for e in res.errors)


def test_edited_payload_is_detected(log):
    log.db.execute("UPDATE audit_log SET payload_json = ? WHERE seq = 3", [json.dumps({"i": 999})])
    assert "payload" in errors(log)


def test_edited_row_with_recomputed_payload_hash_is_detected(log):
    from backend.core.util import canonical_json, sha256_hex

    p = canonical_json({"i": 999})
    log.db.execute("UPDATE audit_log SET payload_json = ?, payload_hash = ? WHERE seq = 3", [p, sha256_hex(p)])
    assert "row_hash mismatch" in errors(log)


def test_edited_actor_is_detected(log):
    log.db.execute("UPDATE audit_log SET actor = 'owner' WHERE seq = 2")
    assert "row_hash mismatch" in errors(log)


def test_deleted_middle_row_is_detected(log):
    log.db.execute("DELETE FROM audit_log WHERE seq = 3")
    assert "sequence gap" in errors(log)


def test_deleted_last_row_is_detected_via_head(log):
    log.db.execute("DELETE FROM audit_log WHERE seq = 5")
    assert "head mismatch" in errors(log)


def test_reordered_rows_are_detected(log):
    log.db.execute("UPDATE audit_log SET seq = 100 WHERE seq = 2")
    log.db.execute("UPDATE audit_log SET seq = 2 WHERE seq = 4")
    log.db.execute("UPDATE audit_log SET seq = 4 WHERE seq = 100")
    assert "prev_hash" in errors(log)


def test_api_verify_fails_after_editing_one_row():
    ct = make_container()
    with make_client(ct) as client:
        client.post("/chat", json={"company_id": "devco", "message": "cash?"})
        good = client.get("/audit/verify").json()
        assert good["ok"] is True and good["checked"] > 3
        ct.db.execute("UPDATE audit_log SET action = 'nothing_to_see' WHERE seq = 2")
        bad = client.get("/audit/verify").json()
    assert bad["ok"] is False and bad["errors"][0]["seq"] == 2
