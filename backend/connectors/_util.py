"""Shared parsing helpers for file-based connectors (dates, amounts, tables with junk rows).

Everything here is deterministic: no LLM, no network. Source text is treated as data.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

# ------------------------------------------------------------------ numbers
_BANGLA = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_CCY_WORDS = re.compile(r"(?i)\b(bdt|cny|rmb|gbp|usd|eur|inr|taka|tk|yuan|rs|cr|dr)\b\.?")
_SYMBOLS = re.compile(r"[৳£$€¥￥₹]")


def to_decimal(value: Any) -> Optional[Decimal]:
    """Parse a messy money string: '12,500', 'Tk 12500', '12500/-', '(1,200.50)', '1.234,56', Bangla digits.

    Returns None for blank / unparseable input (callers decide whether that is an error).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int,)):
        return Decimal(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return Decimal(repr(round(value, 6)))
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "null", "-", "--", "n/a", "na"):
        return None
    s = s.translate(_BANGLA).translate(_ARABIC_INDIC)
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    low = s.lower()
    if re.search(r"\bdr\.?$", low):          # "100.00 Dr" -> debit; sign handled by caller's columns
        s = s[:-2]
    s = _CCY_WORDS.sub("", s)
    s = _SYMBOLS.sub("", s)
    s = s.replace(" ", " ").strip()
    s = re.sub(r"/-+$", "", s).strip()       # "12500/-"
    s = s.replace(" ", "")
    if s.endswith("-") and len(s) > 1:       # trailing minus
        neg, s = True, s[:-1]
    if s.startswith("+"):
        s = s[1:]
    if s.startswith("-"):
        neg, s = (not neg) if s.startswith("--") else True, s.lstrip("-")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):      # 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:                                # 1,234.56
            s = s.replace(",", "")
    elif "," in s:
        if re.fullmatch(r"\d{1,3}(,\d{2,3})+", s):   # 1,234 / 1,00,000
            s = s.replace(",", "")
        elif re.fullmatch(r"\d+,\d{1,2}", s):        # 12,5 / 12,50 (decimal comma)
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d


def money_str(d: Optional[Decimal], places: int = 2) -> Optional[str]:
    if d is None:
        return None
    q = Decimal(1).scaleb(-places)
    return str(d.quantize(q))


# ------------------------------------------------------------------ dates
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _mk(y: int, m: int, d: int) -> Optional[dt.date]:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def _year(y: int) -> int:
    return y + 2000 if y < 100 else y


def parse_date(value: Any, *, dayfirst: bool = True, ref: tuple[int, int] | None = None) -> Optional[dt.date]:
    """Parse the date formats small businesses actually type.

    `ref=(year, month)` is context (e.g. the month tab a row sits in): it fills a missing year
    ('5-Sep') and resolves ambiguous day/month pairs ('03/01/2024' in a March tab is 1 March).
    Without context, ambiguous pairs follow `dayfirst`.
    """
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.date()
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            return None
        if 20000 < float(value) < 80000:     # Excel serial day
            return (dt.datetime(1899, 12, 30) + dt.timedelta(days=float(value))).date()
        return None
    s = str(value).strip().translate(_BANGLA)
    if not s or s.lower() in ("nan", "none", "nat"):
        return None
    s = s.split("T")[0] if re.match(r"^\d{4}-\d{2}-\d{2}T", s) else s
    s = re.sub(r"\s+(UTC|GMT)$", "", s)
    s = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?(\s*[AaPp][Mm])?$", "", s)     # drop a trailing time
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[\s\-/.]([A-Za-z]{3,9})[\s\-/.,]*(\d{2,4})?", s)
    if m and m[2][:3].lower() in _MONTHS:
        y = _year(int(m[3])) if m[3] else (ref[0] if ref else None)
        return _mk(y, _MONTHS[m[2][:3].lower()], int(m[1])) if y else None
    m = re.fullmatch(r"([A-Za-z]{3,9})[\s\-/.]+(\d{1,2})(?:st|nd|rd|th)?[\s,\-/.]+(\d{2,4})", s)
    if m and m[1][:3].lower() in _MONTHS:
        return _mk(_year(int(m[3])), _MONTHS[m[1][:3].lower()], int(m[2]))
    m = re.fullmatch(r"(\d{1,2})[\-/.](\d{1,2})[\-/.](\d{2,4})", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), _year(int(m[3]))
        if a > 12 and b <= 12:
            return _mk(y, b, a)
        if b > 12 and a <= 12:
            return _mk(y, a, b)
        if a == b:
            return _mk(y, a, b)
        if ref is not None:                  # both <= 12: use the sheet's month as the tie-breaker
            if b == ref[1] and a != ref[1]:
                return _mk(y, b, a)
            if a == ref[1] and b != ref[1]:
                return _mk(y, a, b)
        return _mk(y, b, a) if dayfirst else _mk(y, a, b)
    return None


# ------------------------------------------------------------------ text helpers
def norm_header(h: Any) -> str:
    """Lower-case, strip units in brackets and punctuation: 'Credit (BDT)' -> 'credit'."""
    s = str(h if h is not None else "").strip().lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9ঀ-৿一-鿿]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def header_currency(h: Any) -> Optional[str]:
    m = re.search(r"\(([A-Za-z]{3})\)", str(h or ""))
    return m.group(1).upper() if m else None


def clean(value: Any) -> Optional[str]:
    """Strip text; blank/NaN -> None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    s = str(value).strip()
    return s or None


def mask_account(ref: Any) -> Optional[str]:
    """Mask a provider account label to its last four characters: '1234567890' -> '****7890'."""
    s = clean(ref)
    if not s:
        return None
    tail = re.sub(r"[^A-Za-z0-9]", "", s)[-4:]
    return f"****{tail}" if tail else None


# ------------------------------------------------------------------ tables
def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("latin-1")


def _sniff(text: str) -> str:
    head = "\n".join(text.splitlines()[:30])
    try:
        return csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _is_text_cell(v: Any) -> bool:
    s = clean(v)
    if not s:
        return False
    return to_decimal(s) is None and parse_date(s) is None


def detect_header_row(rows: list[list[Any]], max_scan: int = 40) -> int:
    """Index of the header row among the first rows: the first row that is (nearly) fully text and
    as wide as the widest of the rows that follow. Skips titles ('Hisab January 2024') and blanks."""
    scan = rows[:max_scan]
    widths = [sum(1 for v in r if clean(v)) for r in scan]
    if not widths or max(widths) == 0:
        return 0
    target = max(2, int(0.6 * max(widths)))
    for i, r in enumerate(scan):
        n = sum(1 for v in r if clean(v))
        texts = sum(1 for v in r if _is_text_cell(v))
        if n >= target and texts >= 0.8 * n:
            return i
    return 0


def rows_to_frame(rows: list[list[Any]], header_row: int | None = None) -> tuple[pd.DataFrame, int]:
    """Turn raw rows into a DataFrame with the detected header. Adds `_row` = 1-based record number
    in the source (the lineage id), and `_header_row`. Blank rows are dropped (their numbers kept)."""
    if not rows:
        return pd.DataFrame(), 0
    h = detect_header_row(rows) if header_row is None else header_row
    header = [clean(c) or f"col{j + 1}" for j, c in enumerate(rows[h])]
    seen: dict[str, int] = {}
    for j, name in enumerate(header):                   # duplicate header names get a suffix
        if name in seen:
            seen[name] += 1
            header[j] = f"{name}.{seen[name]}"
        else:
            seen[name] = 0
    data, nums = [], []
    for i in range(h + 1, len(rows)):
        r = list(rows[i]) + [None] * (len(header) - len(rows[i]))
        r = r[: len(header)]
        if not any(clean(v) for v in r):
            continue
        data.append(r)
        nums.append(i + 1)
    df = pd.DataFrame(data, columns=header, dtype=object)
    df["_row"] = nums
    return df, h + 1


def read_csv_rows(source: str | Path | bytes | io.IOBase) -> list[list[str]]:
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    elif isinstance(source, bytes):
        raw = source
    else:
        raw = source.read()
        raw = raw.encode("utf-8") if isinstance(raw, str) else raw
    text = _decode(raw)
    delim = _sniff(text)
    return [r for r in csv.reader(io.StringIO(text, newline=""), delimiter=delim)]


def read_sheet_names(path: str | Path) -> list[str]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def read_excel_rows(path: str | Path, sheet: str) -> list[list[Any]]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return [list(r) for r in wb[sheet].iter_rows(values_only=True)]
    finally:
        wb.close()


def read_table(path: str | Path, sheet: str | None = None, header_row: int | None = None) -> tuple[pd.DataFrame, int]:
    """Read a CSV / TSV / XLSX into a frame of raw cell values (+ `_row`). Returns (frame, header_row_number)."""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        names = read_sheet_names(p)
        rows = read_excel_rows(p, sheet or names[0])
    else:
        rows = read_csv_rows(p)
    return rows_to_frame(rows, header_row)


def first_present(df: pd.DataFrame, names: Iterable[str] | str | None) -> Optional[str]:
    """First of `names` that is a column of df (exact, then case/format-insensitive)."""
    if names is None:
        return None
    if isinstance(names, str):
        names = [names]
    cols = list(df.columns)
    for n in names:
        if n in cols:
            return n
    lookup = {norm_header(c): c for c in cols}
    for n in names:
        if norm_header(n) in lookup:
            return lookup[norm_header(n)]
    return None
