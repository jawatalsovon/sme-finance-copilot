import pytest

from backend.agent.tools import ToolRegistry, ToolResult, ToolSpec
from backend.security.approvals import ApprovalError
from backend.security.tiers import MoneyMovementForbidden, Tier
from backend.testing import make_client, make_container

DRAFT_ARGS = {
    "entry_date": "2026-09-28", "memo": "Record Q3 bank charges",
    "reason": "Bank statement shows a charge not in the books",
    "lines": [{"account_id": "devco-acc-6200", "debit": 3500}, {"account_id": "devco-acc-1010", "credit": 3500}],
    "evidence": ["external_statement_lines:devco-esl-4"],
}


def draft_rows(ct):
    return ct.db.scalar("SELECT COUNT(*) FROM journal_entries WHERE status = 'draft'")


def test_t1_company_cannot_run_t2_tool():
    ct = make_container()
    ct.permissions.set_tier("devco", "T1", actor="test")
    out = ct.executor.execute("devco", "draft_journal_entry", DRAFT_ARGS)
    assert out.status == "denied" and "needs tier T2" in out.error
    assert ct.approvals.list("devco") == [] and draft_rows(ct) == 0
    assert ct.audit.list("devco", action="tool_denied")
    # The tool is not even offered to the model at T1.
    assert "draft_journal_entry" not in {s.name for s in ct.tools.schemas(max_tier=Tier.T1)}


def test_t0_company_cannot_run_analysis():
    ct = make_container()
    ct.permissions.set_tier("devco", "T0", actor="test")
    assert ct.executor.execute("devco", "cash_truth", {}).status == "denied"
    assert ct.executor.execute("devco", "account_balances", {}).status == "executed"


def test_t2_goes_to_queue_not_executed_then_owner_approves():
    ct = make_container()  # devco is T2
    out = ct.executor.execute("devco", "draft_journal_entry", DRAFT_ARGS)
    assert out.status == "queued_for_approval" and out.approval_id
    assert draft_rows(ct) == 0, "T2 must not write anything before approval"
    pending = ct.approvals.list("devco", "pending")
    assert len(pending) == 1 and pending[0]["payload"]["args"] == DRAFT_ARGS

    with make_client(ct) as client:
        r = client.post(f"/approvals/{out.approval_id}/approve", headers={"X-Actor": "owner:jawat"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "approved" and r.json()["decided_by"] == "owner:jawat"
        assert client.post(f"/approvals/{out.approval_id}/approve").status_code == 409  # no double execution
    assert draft_rows(ct) == 1
    lines = ct.db.query_dicts("SELECT jl.* FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id "
                              "WHERE je.status = 'draft' ORDER BY line_no")
    assert [float(l["amount_base"]) for l in lines] == [3500.0, -3500.0]
    assert lines[0]["source_system"] == "copilot" and lines[0]["source_file"] == f"approval:{out.approval_id}"
    assert ct.audit.verify_chain().ok


def test_reject_never_executes():
    ct = make_container()
    out = ct.executor.execute("devco", "draft_journal_entry", DRAFT_ARGS)
    with make_client(ct) as client:
        r = client.post(f"/approvals/{out.approval_id}/reject", json={"note": "not now"})
    assert r.json()["status"] == "rejected" and draft_rows(ct) == 0


def test_unbalanced_draft_is_refused_before_queueing():
    ct = make_container()
    bad = {**DRAFT_ARGS, "lines": [{"account_id": "devco-acc-6200", "debit": 3500},
                                   {"account_id": "devco-acc-1010", "credit": 3000}]}
    out = ct.executor.execute("devco", "draft_journal_entry", bad)
    assert out.status == "denied" and "does not balance" in out.error
    assert ct.approvals.list("devco") == []


def test_tampered_approval_payload_is_not_executed():
    ct = make_container()
    out = ct.executor.execute("devco", "draft_journal_entry", DRAFT_ARGS)
    ct.db.execute("UPDATE approvals SET payload_json = replace(payload_json, '3500', '350000') WHERE id = ?",
                  [out.approval_id])
    with pytest.raises(ApprovalError, match="modified"):
        ct.executor.execute_approved(out.approval_id, "owner")
    assert draft_rows(ct) == 0
    assert ct.audit.list(action="approval_tamper_detected")


def test_tier_downgrade_after_request_blocks_execution():
    ct = make_container()
    out = ct.executor.execute("devco", "draft_journal_entry", DRAFT_ARGS)
    ct.permissions.set_tier("devco", "T1", actor="owner")
    with pytest.raises(ApprovalError):
        ct.executor.execute_approved(out.approval_id, "owner")
    assert draft_rows(ct) == 0 and ct.approvals.get(out.approval_id)["status"] == "failed"


def test_t3_actions_are_capped_per_day():
    ct = make_container(t3_daily_cap=2)
    ct.permissions.set_tier("devco", "T3", actor="test")
    args = {"channel": "email", "body": "Friendly reminder about INV-001", "invoice_id": "devco-inv-001"}
    assert [ct.executor.execute("devco", "mark_reminder_ready", args).status for _ in range(3)] == \
        ["executed", "executed", "denied"]
    assert ct.db.scalar("SELECT COUNT(*) FROM reminders WHERE status = 'ready'") == 2


def test_model_cannot_choose_company_or_unknown_args():
    ct = make_container()
    out = ct.executor.execute("devco", "account_balances", {"company_id": "other-co"})
    assert out.status == "denied" and "unknown argument" in out.error
    assert ct.executor.execute("devco", "no_such_tool", {}).status == "denied"


def test_registry_refuses_any_money_moving_tool():
    reg = ToolRegistry()
    noop = lambda ctx, args: ToolResult()  # noqa: E731
    for name, desc, flag in [("transfer_funds", "move money", False), ("pay_supplier", "pay a bill", False),
                             ("helper", "harmless", True), ("bank_tool", "Initiate payment to vendor", False)]:
        with pytest.raises(MoneyMovementForbidden):
            reg.register(ToolSpec(name, desc, Tier.T3, {"type": "object", "properties": {}}, noop, moves_money=flag))
    # And the shipped registry contains none.
    from backend.agent.tools import default_registry
    from backend.security.tiers import looks_like_money_movement

    assert not [t.name for t in default_registry().all() if looks_like_money_movement(t.name, t.description)]


def test_no_tool_lets_the_agent_approve():
    from backend.agent.tools import default_registry

    names = {t.name for t in default_registry().all()}
    assert not [n for n in names if "approv" in n]
