"""Bank / wallet / generic statement connectors on small hand-made files (messy on purpose)."""
import pandas as pd
import pytest

from backend.connectors.base import ConnectorError, registry

registry.autodiscover()

BANK_WD = """Mirpur Commercial Bank - Statement
Account 0123456789
Period 01/03/2024 - 31/03/2024

Txn Date,Value Date,Narration,Chq/Ref No,Withdrawal,Deposit,Balance
01-Mar-2024,01-Mar-2024,OPENING BALANCE,,,"1,00,000.00","1,00,000.00"
02-Mar-2024,02-Mar-2024,"RENT, MARCH",CHQ 123,"12,500.00",,"87,500.00"
03-Mar-2024,03-Mar-2024,POS SETTLEMENT,,,"5,000.50","92,500.50"
"""
WALLET = """TrxID,Date,Time,Transaction Type,Counterparty,Reference,Credit (BDT),Debit (BDT),Fee (BDT),Balance (BDT)
A1,01/03/2024,08:00,Payment received,01700XXX111,INV1,"1,000.00",,15.00,"41,985.00"
A2,01/03/2024,09:00,Send money,01700XXX222,PAY2,,500.00,5.00,"41,480.00"
"""
WISE = """TransferWise ID,Date,Amount,Currency,Description,Payment Reference,Running Balance,Total fees,Payer Name,Payee Name
W1,05-03-2024,"1,549.12",EUR,Payout from settlement,SETL,"8,549.12",0.00,Amazon Payments,
W2,18-03-2024,"-5,500.00",EUR,Converted EUR to CNY,CONV,"3,049.12",30.25,,Wise Asia-Pacific
"""
SIGNED_US = """Date,Description,Amount (GBP),Balance
12/31/2024,Card payment,(45.10),954.90
01/02/2025,Refund,10.00,964.90
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _pull(cid, tmp_path, name, text, **cfg):
    c = registry.create(cid, "co", {"path": _write(tmp_path, name, text), "profiles_dir": str(tmp_path / "p"), **cfg})
    return c.pull()


def test_bank_withdrawal_deposit_with_junk_header_and_lakh_separators(tmp_path):
    res = _pull("bank_csv", tmp_path, "mirpur.csv", BANK_WD, base_currency="BDT", account_id="co-1100",
                statement_ref="0123456789")
    f = res.frames["external_statement_lines"]
    assert f["amount"].tolist() == ["100000.00", "-12500.00", "5000.50"]
    assert f["balance"].tolist() == ["100000.00", "87500.00", "92500.50"]
    assert f["source_row_id"].tolist() == ["6", "7", "8"]                 # real row numbers, junk rows included
    assert set(f["statement_ref"]) == {"****6789"} and set(f["account_id"]) == {"co-1100"}
    assert f["description"].tolist()[1] == "RENT, MARCH"                  # quoted comma survives
    assert not res.warnings


def test_wallet_fee_is_deducted_from_balance_and_continuity_checked(tmp_path):
    res = _pull("wallet_csv", tmp_path, "bkash.csv", WALLET, base_currency="BDT")
    f = res.frames["external_statement_lines"]
    assert f["channel"].tolist() == ["wallet", "wallet"]
    assert f["amount"].tolist() == ["985.00", "-505.00"] and f["fee"].tolist() == ["15.00", "5.00"]
    assert f["balance"].tolist() == ["41985.00", "41480.00"] and not res.warnings


def test_balance_break_is_reported_not_hidden(tmp_path):
    broken = WALLET.replace('"41,480.00"', '"40,000.00"')
    res = _pull("wallet_csv", tmp_path, "bkash.csv", broken, base_currency="BDT")
    assert any("running balance does not follow" in w for w in res.warnings)


def test_wise_multi_currency_needs_and_uses_fx(tmp_path):
    with pytest.raises(ValueError, match="no FX rate"):
        _pull("wallet_csv", tmp_path, "wise.csv", WISE, base_currency="CNY")
    res = _pull("wallet_csv", tmp_path, "wise.csv", WISE, base_currency="CNY", fx_rates={"EUR": "7.8"})
    f = res.frames["external_statement_lines"]
    assert f["currency"].tolist() == ["EUR", "EUR"] and f["amount"].tolist() == ["1549.12", "-5500.00"]
    assert f["amount_base"].tolist() == ["12083.14", "-42900.00"] and f["fee"].tolist() == ["0.00", "30.25"]
    assert f["counterparty_text"].tolist() == ["Amazon Payments", "Wise Asia-Pacific"]


def test_dated_fx_rates_use_latest_rate_on_or_before_the_day(tmp_path):
    rates = pd.DataFrame([{"rate_date": "2024-03-01", "from_currency": "EUR", "to_currency": "CNY", "rate": "7.0"},
                          {"rate_date": "2024-03-10", "from_currency": "EUR", "to_currency": "CNY", "rate": "8.0"}])
    f = _pull("wallet_csv", tmp_path, "wise.csv", WISE, base_currency="CNY", fx_rates=rates).frames["external_statement_lines"]
    assert f["fx_rate"].tolist() == ["7.0", "8.0"]


def test_signed_amount_parentheses_month_first_dates_and_header_currency(tmp_path):
    res = _pull("csv_upload", tmp_path, "us.csv", SIGNED_US, base_currency="GBP")
    f = res.frames["external_statement_lines"]
    assert f["txn_date"].astype(str).tolist() == ["2024-12-31", "2025-01-02"]     # 12/31 forces month-first for the file
    assert f["amount"].tolist() == ["-45.10", "10.00"] and set(f["currency"]) == {"GBP"}
    assert any("NOT yet confirmed" in w for w in res.warnings)                    # guessed layout is flagged


def test_bank_connector_refuses_a_wallet_layout(tmp_path):
    c = registry.create("bank_csv", "co", {"path": _write(tmp_path, "b.csv", WALLET), "base_currency": "BDT"})
    with pytest.raises(ConnectorError, match="does not handle"):
        c.pull()
    assert c.test_connection().ok is False


def test_not_configured_is_a_clean_status():
    c = registry.create("bank_csv", "co", {})
    r = c.test_connection()
    assert r.ok is False and "not configured" in r.message
    with pytest.raises(ConnectorError, match="not configured"):
        c.pull()


def test_unreadable_rows_are_counted_not_dropped_silently(tmp_path):
    bad = BANK_WD + "xx-yy,,GARBAGE ROW,,,,\n"
    res = _pull("bank_csv", tmp_path, "m.csv", bad, base_currency="BDT")
    assert len(res.frames["external_statement_lines"]) == 3
    assert any("1 rows skipped" in w and "9" in w for w in res.warnings)


def test_reloading_the_same_file_gives_the_same_ids(tmp_path):
    a = _pull("bank_csv", tmp_path, "m.csv", BANK_WD, base_currency="BDT").frames["external_statement_lines"]
    b = _pull("bank_csv", tmp_path, "m.csv", BANK_WD, base_currency="BDT").frames["external_statement_lines"]
    assert a["id"].tolist() == b["id"].tolist() and a["id"].is_unique


def test_lines_load_into_the_ledger_with_lineage(tmp_path):
    from backend.ledger.db import connect
    from backend.ledger.loader import load_frames
    res = _pull("bank_csv", tmp_path, "m.csv", BANK_WD, base_currency="BDT")
    db = connect(":memory:")
    reports = load_frames(db, res.frames, company_id="co", if_exists="skip")
    assert reports["external_statement_lines"].inserted == 3 and reports["external_statement_lines"].rejected_count == 0
    again = load_frames(db, res.frames, company_id="co", if_exists="skip")
    assert again["external_statement_lines"].inserted == 0 and again["external_statement_lines"].skipped_existing == 3
