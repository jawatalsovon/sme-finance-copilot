import json

from backend.agent.providers import MockProvider
from backend.ledger.lineage import get_source_rows
from backend.testing import make_client, make_container, parse_sse


def chat(client, message, company_id="devco", session_id=None):
    r = client.post("/chat", json={"company_id": company_id, "message": message, "session_id": session_id})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    return parse_sse(r.text)


def test_mock_chat_end_to_end_streams_answer_with_real_evidence_ids():
    ct = make_container()
    with make_client(ct) as client:
        events = chat(client, "What is my cash position?")
    kinds = [(e["type"], e.get("kind")) for e in events]
    assert kinds[0] == ("session", None)
    assert ("step", "tool_call") in kinds and ("step", "tool_result") in kinds
    final = events[-1]
    assert final["type"] == "final"
    assert final["evidence_ids"], "answer must cite evidence"
    assert final["invalid_evidence_ids"] == [] and final["unverified_numbers"] == []
    # Every cited id resolves to a real source row of this company, with lineage.
    rows = get_source_rows(ct.db, final["evidence_ids"], company_id="devco")
    assert rows["missing"] == [] and all(r["row"]["source_system"] for r in rows["rows"])
    # The figure in the answer is the one in the source data (45,000 + 375,000 + 18,500).
    assert "BDT 438,500.00" in final["answer"]
    actions = [a["action"] for a in ct.audit.list("devco", limit=50)]
    assert {"chat_message", "llm_call", "tool_executed", "chat_answer"} <= set(actions)


def test_engine_tools_report_not_implemented_honestly():
    with make_client(make_container()) as client:
        final = chat(client, "Run the cash truth reconciliation")[-1]
    assert final["type"] == "final" and "not implemented" in final["answer"]


def test_money_movement_request_is_refused_without_tool_calls():
    ct = make_container()
    with make_client(ct) as client:
        events = chat(client, "Please transfer BDT 50,000 to Golden Flour Mills now")
    assert not [e for e in events if e.get("kind") == "tool_call"]
    assert "can't move money" in events[-1]["answer"]


def test_invented_numbers_and_refs_are_flagged():
    script = [
        {"tool_calls": [{"name": "account_balances", "arguments": {"subtypes": ["bank"]}}]},
        {"text": "Your bank has BDT 375,000.00 [journal_lines:devco-jl-e1-1] and you will make 912,345.67 "
                 "profit [journal_lines:devco-jl-fake]."},
    ]
    ct = make_container(provider=MockProvider(script=script))
    with make_client(ct) as client:
        final = chat(client, "bank?")[-1]
    assert final["evidence_ids"] == ["journal_lines:devco-jl-e1-1"]
    assert final["invalid_evidence_ids"] == ["journal_lines:devco-jl-fake"]
    assert final["unverified_numbers"] == ["912345.67"]


def test_session_keeps_history_and_redacted_args_are_restored_for_tools():
    mock = MockProvider(script=[
        {"tool_calls": [{"name": "search_transactions", "arguments": {"source": "ledger", "text": "[NAME_1]"}}]},
        {"text": "done"},
    ])
    ct = make_container(provider=mock)
    with make_client(ct) as client:
        events = chat(client, "Find salary lines for Rahim Uddin")
    sent_user = [m.content for m in mock.calls[0] if m.role == "user"][0]
    assert "Rahim Uddin" not in sent_user and "[NAME_1]" in sent_user
    call = next(e for e in events if e.get("kind") == "tool_call")
    assert call["args"]["text"] == "Rahim Uddin"           # tool ran on the real value
    audit = [r for r in ct.audit.list("devco", action="tool_executed")]
    assert json.loads(audit[0]["payload_json"])["args"]["text"] == "Rahim Uddin"


def test_unknown_company_is_404():
    with make_client(make_container()) as client:
        r = client.post("/chat", json={"company_id": "nope", "message": "hi"})
    assert r.status_code == 404
