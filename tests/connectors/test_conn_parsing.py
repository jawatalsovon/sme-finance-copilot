import datetime as dt
from decimal import Decimal

import pytest

from backend.connectors._util import (detect_header_row, header_currency, mask_account, norm_header, parse_date,
                                      rows_to_frame, to_decimal)


@pytest.mark.parametrize("raw,expected", [
    ("12,500", "12500"), ("Tk 12500", "12500"), ("12500/-", "12500"), ("১২৫০০", "12500"),
    ("(1,200.50)", "-1200.50"), ("1.234,56", "1234.56"), ("1,00,000", "100000"), ("12,5", "12.5"),
    ("5.00-", "-5.00"), (" £ 3,246.10 ", "3246.10"), ("¥12,000", "12000"), (2147, "2147"), (225.5, "225.5"),
    ("BDT 9,466", "9466"), ("-7", "-7"),
])
def test_to_decimal_messy_amounts(raw, expected):
    assert to_decimal(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [None, "", "  ", "n/a", "-", float("nan"), "abc"])
def test_to_decimal_blank_or_text_is_none(raw):
    assert to_decimal(raw) is None


def test_parse_date_formats_and_context():
    assert parse_date("31/01/2024") == dt.date(2024, 1, 31)
    assert parse_date("12/31/2024") == dt.date(2024, 12, 31)           # only readable month-first
    assert parse_date("01-Jan-2024") == dt.date(2024, 1, 1)
    assert parse_date("31.03.24") == dt.date(2024, 3, 31)
    assert parse_date("2024-01-25 23:59:59 UTC") == dt.date(2024, 1, 25)
    assert parse_date("5-Sep", ref=(2024, 9)) == dt.date(2024, 9, 5)
    assert parse_date("5-Sep") is None                                  # no year, no context: refuse to guess
    assert parse_date(dt.datetime(2024, 2, 3, 4, 5)) == dt.date(2024, 2, 3)
    assert parse_date(45292) == dt.date(2024, 1, 1)                     # Excel serial
    # 03/01 vs 01/03 in the March tab of a notebook: the tab decides, both mean 1 March
    assert parse_date("03/01/2024", ref=(2024, 3)) == dt.date(2024, 3, 1)
    assert parse_date("01/03/2024", ref=(2024, 3)) == dt.date(2024, 3, 1)
    assert parse_date("01/03/2024") == dt.date(2024, 3, 1)              # dayfirst default
    assert parse_date("01/03/2024", dayfirst=False) == dt.date(2024, 1, 3)
    assert parse_date("not a date") is None


def test_header_row_detection_skips_titles_and_blank_rows():
    rows = [["Mirpur Family Store - Hisab January 2024", None, None], [None, None, None], ["Date", "Details", "In"],
            ["01/01/2024", "Cash sales", "1,000"], [None, None, None], ["02/01/2024", "Rent", None]]
    assert detect_header_row(rows) == 2
    df, hdr = rows_to_frame(rows)
    assert hdr == 3 and list(df.columns[:3]) == ["Date", "Details", "In"]
    assert df["_row"].tolist() == [4, 6]                                 # source row numbers survive blank rows


def test_header_helpers():
    assert norm_header("Credit (BDT)") == "credit"
    assert header_currency("Debit (CNY)") == "CNY" and header_currency("Debit") is None
    assert mask_account("1234 5678 90") == "****7890" and mask_account(None) is None
