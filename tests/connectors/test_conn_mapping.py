import json

import pandas as pd
import pytest

from backend.agent.providers.mock import MockProvider
from backend.connectors._mapping import (MappingProfile, ProfileStore, propose_mapping, propose_mapping_llm, shape)
from backend.connectors.base import registry

registry.autodiscover()

BANK = """Some Bank plc - statement
Account no 1234567890

Posted,Memo/Notes,Out,In,Running
03/01/2024,Karim Traders rent,"12,500.00",,"87,500.00"
04/01/2024,Customer deposit,,"5,000.50","92,500.50"
"""


def _file(tmp_path, text=BANK, name="odd.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_heuristic_mapping_and_unconfirmed_pull_warns(tmp_path):
    p = _file(tmp_path)
    c = registry.create("bank_csv", "co", {"path": str(p), "base_currency": "GBP", "profiles_dir": str(tmp_path / "prof")})
    prop = c.propose(p)
    assert prop.source == "heuristic" and prop.confirmed is False
    assert prop.columns["date"] == "Posted" and prop.columns["description"] == "Memo/Notes"
    # 'Out'/'In'/'Running' are short names: they must still be found as debit/credit/balance
    assert prop.columns["debit"] == "Out" and prop.columns["credit"] == "In" and prop.columns["balance"] == "Running"


def test_llm_proposal_is_validated_and_sees_only_shapes(tmp_path):
    p = _file(tmp_path)
    df = pd.read_csv(p, skiprows=3, dtype=str)
    df["_row"] = range(5, 5 + len(df))
    reply = json.dumps({"columns": {"date": "Posted", "description": "Memo/Notes", "debit": "Out", "credit": "In",
                                    "balance": "Running", "reference": "Nonexistent column", "bogus_field": "Posted"},
                        "amount_mode": "split", "dayfirst": False, "currency": "gbp", "notes": ["looks like a UK bank"]})
    provider = MockProvider(script=[{"text": "Sure! " + reply}])
    prop = propose_mapping_llm(provider, df, "statement")
    assert prop.source == "llm" and prop.options["dayfirst"] is False and prop.options["currency"] == "GBP"
    assert "reference" not in prop.columns and "bogus_field" not in prop.columns       # hallucinations dropped
    assert any("ignored LLM suggestions" in n for n in prop.notes)
    sent = " ".join(m.content for m in provider.calls[0])
    assert "Karim" not in sent and "12,500" not in sent                            # values never leave the machine
    assert shape("Karim 12,500.50") == "Aaaaa 99,999.99"


def test_llm_garbage_falls_back_to_heuristic(tmp_path):
    p = _file(tmp_path)
    df = pd.read_csv(p, skiprows=3, dtype=str)
    prop = propose_mapping_llm(MockProvider(script=[{"text": "I cannot do that"}]), df, "statement")
    assert prop.columns["date"] == "Posted" and any("not valid JSON" in n for n in prop.notes)


def test_owner_confirms_and_profile_is_reused(tmp_path):
    p = _file(tmp_path)
    prof_dir = tmp_path / "prof"
    c = registry.create("bank_csv", "co", {"path": str(p), "base_currency": "GBP", "profiles_dir": str(prof_dir)})
    prop = c.propose(p)
    prop.options["dayfirst"] = True
    saved = c.confirm(prop, "my_bank_layout", "My bank", p)
    assert (prof_dir / "my_bank_layout.json").exists() and saved.target == "statement"
    # a fresh connector now recognises the layout without any guessing
    c2 = registry.create("bank_csv", "co", {"path": str(p), "base_currency": "GBP", "profiles_dir": str(prof_dir)})
    res = c2.pull()
    assert res.column_mapping["_profile"] == "profile:my_bank_layout" and not res.warnings
    f = res.frames["external_statement_lines"]
    assert f["amount"].tolist() == ["-12500.00", "5000.50"] and f["balance"].tolist() == ["87500.00", "92500.50"]
    assert f["txn_date"].astype(str).tolist() == ["2024-01-03", "2024-01-04"]
    assert f["source_row_id"].tolist() == ["5", "6"]                                    # junk header rows counted
    assert set(f["source_system"]) == {"bank_csv"} and set(f["source_file"]) == {"odd.csv"}


def test_builtin_profiles_cannot_be_overwritten(tmp_path):
    store = ProfileStore(tmp_path)
    with pytest.raises(ValueError):
        store.save(MappingProfile(id="bkash_wallet", name="x"))
    with pytest.raises(ValueError):
        store.save(MappingProfile(id="bad id!", name="x"))
