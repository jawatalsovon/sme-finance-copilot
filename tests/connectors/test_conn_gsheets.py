"""Google Sheets connector: shared-link export (HTTP mocked), not-configured status, service-account guard."""

import httpx
import pandas as pd
import pytest

from backend.connectors.base import ConnectorError, registry
from tests.connectors.test_conn_ledger_files import _notebook

registry.autodiscover()
SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?usp=sharing"
ACCOUNTS = pd.DataFrame([{"id": f"co-{c}", "code": c, "name": n, "type": "asset", "subtype": s, "currency": "BDT"}
                         for c, n, s in [("1000", "Cash", "cash"), ("3000", "Capital", "owner_equity"),
                                         ("4000", "Sales", "revenue"), ("1300", "Stock", "inventory"), ("2000", "AP", "ap")]])


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_shared_link_is_fetched_once_as_a_workbook_and_read(tmp_path):
    xlsx = open(_notebook(tmp_path), "rb").read()
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        return httpx.Response(200, content=xlsx, headers={"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})

    c = registry.create("gsheets", "co", {"url": URL, "http_client": _client(handler), "cache_dir": str(tmp_path / "cache"),
                                          "base_currency": "BDT", "accounts": ACCOUNTS, "profiles_dir": str(tmp_path / "p")})
    res = c.pull()
    assert len(res.frames["journal_entries"]) == 3
    assert seen[0].startswith(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export") and "format=xlsx" in seen[0]
    assert res.source_files == [f"gsheet_{SHEET_ID[:12]}.xlsx"]                    # lineage names the sheet, not a temp path
    assert set(res.frames["journal_entries"]["source_file"]) == {f"gsheet_{SHEET_ID[:12]}.xlsx"}


def test_private_sheet_gives_a_clear_error(tmp_path):
    def handler(req):
        return httpx.Response(200, text="<html>Sign in</html>", headers={"content-type": "text/html"})

    c = registry.create("gsheets", "co", {"url": URL, "http_client": _client(handler), "cache_dir": str(tmp_path)})
    with pytest.raises(ConnectorError, match="Share it"):
        c.pull()
    ok = c.test_connection()
    assert ok.ok is False and "Share it" in ok.message


def test_not_configured_and_bad_link():
    c = registry.create("gsheets", "co", {})
    r = c.test_connection()
    assert r.ok is False and "not configured" in r.message
    with pytest.raises(ConnectorError, match="not a Google Sheets link"):
        registry.create("gsheets", "co", {"url": "https://example.com/x"}).paths()


def _has_google_auth() -> bool:
    try:
        import google.auth  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(_has_google_auth(), reason="google-auth installed")
def test_service_account_mode_says_what_is_missing(tmp_path):
    c = registry.create("gsheets", "co", {"spreadsheet_id": SHEET_ID, "service_account_file": str(tmp_path / "sa.json"),
                                          "cache_dir": str(tmp_path)})
    with pytest.raises(ConnectorError, match="google-auth"):
        c.pull()


def test_file_fallback_needs_no_network(tmp_path):
    def boom(req):
        raise AssertionError("no HTTP expected")

    c = registry.create("gsheets", "co", {"path": _notebook(tmp_path), "http_client": _client(boom), "base_currency": "BDT",
                                          "accounts": ACCOUNTS, "profiles_dir": str(tmp_path / "p")})
    assert len(c.pull().frames["journal_entries"]) == 3
