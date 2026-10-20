"""Google Sheets (read-only).

Three ways to hand over a sheet, in the order they are tried:
  1. `path`                    a downloaded .xlsx / .csv (file fallback; also what the demo and tests use)
  2. `url` / `spreadsheet_id`  a sheet shared as "anyone with the link can view": the whole workbook is fetched
                               once through Google's export URL and read like any Excel file (all tabs)
  3. service account           `service_account_file` (or env GOOGLE_SERVICE_ACCOUNT_FILE) + `spreadsheet_id`:
                               the owner shares the sheet with the service account's e-mail; needs `google-auth`
Nothing is ever written back to the sheet. The sheet's tabs are read with the same mapping profiles as any file
(for example the cash-book notebook profile), so the rows keep `file!tab!row` lineage.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Optional

import httpx

from backend.connectors.base import (ConnectionTestResult, ConnectorError, ConnectorMetadata, env_value, register)
from backend.connectors.csv_upload import CsvUploadConnector

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def sheet_id_from(url_or_id: str) -> str:
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", url_or_id)
    if m:
        return m[1]
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", url_or_id.strip()):
        return url_or_id.strip()
    raise ConnectorError("not a Google Sheets link or id")


@register
class GoogleSheetsConnector(CsvUploadConnector):
    metadata = ConnectorMetadata(
        id="gsheets", name="Google Sheets (read)", category="spreadsheet", auth_type="service_account", status="file",
        description="Reads the owner's Google-Sheets bookkeeping (e.g. a cash-book notebook with one tab per month): from "
                    "a shared link, with a service account, or from a downloaded copy. Messy dates, amounts like "
                    "'Tk 12,500' or Bangla digits and mistyped labels are handled; the owner confirms the account "
                    "labels once. Read-only. Live Google access is fixture-tested; the demo uses the file fallback.",
        canonical_tables=["journal_entries", "journal_lines", "external_statement_lines", "accounts"],
        env_vars=["GOOGLE_SERVICE_ACCOUNT_FILE"], modes=["file", "shared-link", "service-account"])

    # ------------------------------------------------------------------ source selection
    def _client(self) -> httpx.Client:
        return self.config.get("http_client") or httpx.Client(follow_redirects=True, timeout=60)

    def _sheet_ref(self) -> Optional[str]:
        raw = self.config.get("url") or self.config.get("spreadsheet_id")
        return sheet_id_from(raw) if raw else None

    def _sa_file(self) -> str:
        return self.config.get("service_account_file") or env_value("GOOGLE_SERVICE_ACCOUNT_FILE")

    def fetch(self) -> Path:
        sid = self._sheet_ref()
        if not sid:
            raise ConnectorError("not configured: give a downloaded file ('path'), a shared link ('url') or a "
                                 "'spreadsheet_id' with GOOGLE_SERVICE_ACCOUNT_FILE")
        cache = Path(self.config.get("cache_dir") or tempfile.gettempdir()) / "copilot_gsheets"
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / f"gsheet_{sid[:12]}.xlsx"
        client = self._client()
        sa = self._sa_file()
        if sa and not self.config.get("url_only"):
            r = client.get(f"https://www.googleapis.com/drive/v3/files/{sid}/export", params={"mimeType": XLSX_MIME},
                           headers={"Authorization": f"Bearer {self._service_account_token(sa)}"})
        else:
            r = client.get(f"https://docs.google.com/spreadsheets/d/{sid}/export", params={"format": "xlsx"})
        if r.status_code != 200 or "html" in r.headers.get("content-type", "").lower():
            raise ConnectorError("Google did not return the sheet (HTTP %s). Share it as 'anyone with the link: Viewer', "
                                 "or share it with the service account's e-mail address." % r.status_code)
        target.write_bytes(r.content)
        return target

    @staticmethod
    def _service_account_token(path: str) -> str:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
        except ImportError as e:                       # not a hard dependency of the project
            raise ConnectorError("service-account mode needs the 'google-auth' package (pip install google-auth requests)") from e
        creds = service_account.Credentials.from_service_account_file(
            path, scopes=["https://www.googleapis.com/auth/drive.readonly"])
        creds.refresh(Request())
        return creds.token

    def paths(self) -> list[Path]:
        local = super().paths()
        if local:
            return local
        if self._sheet_ref():
            return [self.fetch()]
        return []

    def test_connection(self) -> ConnectionTestResult:
        if not (self.config.get("path") or self.config.get("paths") or self._sheet_ref()):
            return ConnectionTestResult(ok=False, message="not configured: set a downloaded file path, a shared sheet "
                                        "link, or GOOGLE_SERVICE_ACCOUNT_FILE + spreadsheet id")
        try:
            return super().test_connection()
        except ConnectorError as e:
            return ConnectionTestResult(ok=False, message=str(e))
