"""QuickBooks Online: OAuth2 (authorization-code flow) + a small REST client. SANDBOX ONLY.

Credentials come from the environment (backend/.env): QBO_CLIENT_ID, QBO_CLIENT_SECRET, QBO_REALM_ID, QBO_REDIRECT_URI,
QBO_ENV (must be 'sandbox') and, after the one-time authorisation, QBO_REFRESH_TOKEN. Intuit rotates the refresh token on
every refresh, so the newest one is kept in a small token file next to the private data (never in the repo, never logged):
  $COPILOT_PRIVATE_DIR/qbo_tokens.json   or   data/private/qbo_tokens.json
The token file wins over QBO_REFRESH_TOKEN once it exists.

The HTTP client is injectable (httpx.Client) so tests replay recorded-style fixtures and never touch the network.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

import httpx

from backend.connectors.base import ConnectorError, env_value

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
API_SANDBOX = "https://sandbox-quickbooks.api.intuit.com"
SCOPE = "com.intuit.quickbooks.accounting"
MINOR_VERSION = "75"
REPO_ROOT = Path(__file__).resolve().parents[2]


def private_dir() -> Path:
    priv = os.environ.get("COPILOT_PRIVATE_DIR", "").strip()
    return Path(priv) if priv else REPO_ROOT / "data" / "private"


class QboConfig:
    """Credential names are read once; `configured()` says what is missing without exposing any value."""

    def __init__(self, overrides: dict[str, Any] | None = None):
        o = overrides or {}
        self.client_id = o.get("client_id") or env_value("QBO_CLIENT_ID")
        self.client_secret = o.get("client_secret") or env_value("QBO_CLIENT_SECRET")
        self.realm_id = o.get("realm_id") or env_value("QBO_REALM_ID")
        self.env = (o.get("env") or env_value("QBO_ENV") or "sandbox").lower()
        self.redirect_uri = o.get("redirect_uri") or env_value("QBO_REDIRECT_URI") or "http://localhost:8000/connectors/qbo/callback"
        self.refresh_token_env = o.get("refresh_token") or env_value("QBO_REFRESH_TOKEN")
        self.token_file = Path(o["token_file"]) if o.get("token_file") else private_dir() / "qbo_tokens.json"

    def missing(self) -> list[str]:
        need = {"QBO_CLIENT_ID": self.client_id, "QBO_CLIENT_SECRET": self.client_secret,
                "QBO_REALM_ID": self.realm_id}
        out = [k for k, v in need.items() if not v]
        if not self.refresh_token() :
            out.append("QBO_REFRESH_TOKEN")
        return out

    def refresh_token(self) -> str:
        if self.token_file.exists():
            try:
                return json.loads(self.token_file.read_text(encoding="utf-8")).get("refresh_token", "") or self.refresh_token_env
            except Exception:
                pass
        return self.refresh_token_env


class QboClient:
    def __init__(self, cfg: QboConfig, http: httpx.Client | None = None, base_url: str = API_SANDBOX):
        if cfg.env != "sandbox":
            raise ConnectorError("QBO_ENV must be 'sandbox': this copilot never connects to a production QuickBooks company")
        self.cfg = cfg
        self.http = http or httpx.Client(timeout=60)
        self.base_url = base_url.rstrip("/")
        self._access: tuple[str, float] | None = None

    # -------------------------------------------------------------- OAuth
    @staticmethod
    def authorize_url(cfg: QboConfig, state: Optional[str] = None) -> tuple[str, str]:
        state = state or secrets.token_urlsafe(16)
        q = urllib.parse.urlencode({"client_id": cfg.client_id, "response_type": "code", "scope": SCOPE,
                                    "redirect_uri": cfg.redirect_uri, "state": state})
        return f"{AUTH_URL}?{q}", state

    def _basic(self) -> dict[str, str]:
        tok = base64.b64encode(f"{self.cfg.client_id}:{self.cfg.client_secret}".encode()).decode()
        return {"Authorization": f"Basic {tok}", "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded"}

    def _save_tokens(self, body: dict[str, Any]) -> None:
        f = self.cfg.token_file
        f.parent.mkdir(parents=True, exist_ok=True)
        data = {"refresh_token": body.get("refresh_token"), "saved_at": int(time.time()),
                "realm_id": self.cfg.realm_id or None}
        f.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass

    def exchange_code(self, code: str, realm_id: Optional[str] = None) -> None:
        """Last step of the one-time authorisation: trade the callback `code` for tokens and store them."""
        if realm_id:
            self.cfg.realm_id = realm_id
        r = self.http.post(TOKEN_URL, headers=self._basic(),
                           data={"grant_type": "authorization_code", "code": code, "redirect_uri": self.cfg.redirect_uri})
        if r.status_code != 200:
            raise ConnectorError(f"QuickBooks refused the authorisation code (HTTP {r.status_code})")
        body = r.json()
        self._save_tokens(body)
        self._access = (body["access_token"], time.time() + int(body.get("expires_in", 3600)) - 60)

    def access_token(self) -> str:
        if self._access and self._access[1] > time.time():
            return self._access[0]
        rt = self.cfg.refresh_token()
        if not rt:
            raise ConnectorError("no QuickBooks refresh token: run the one-time authorisation first")
        r = self.http.post(TOKEN_URL, headers=self._basic(), data={"grant_type": "refresh_token", "refresh_token": rt})
        if r.status_code != 200:
            raise ConnectorError(f"QuickBooks token refresh failed (HTTP {r.status_code}); re-authorise the connection")
        body = r.json()
        if body.get("refresh_token"):
            self._save_tokens(body)                    # Intuit rotates it; keep the newest
        self._access = (body["access_token"], time.time() + int(body.get("expires_in", 3600)) - 60)
        return self._access[0]

    # -------------------------------------------------------------- REST
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token()}", "Accept": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.base_url}/v3/company/{self.cfg.realm_id}/{path.lstrip('/')}"

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        p = {"minorversion": MINOR_VERSION, **(params or {})}
        r = self.http.get(self._url(path), params=p, headers=self._headers())
        if r.status_code == 429:
            raise ConnectorError("QuickBooks rate limit reached; try again in a minute")
        if r.status_code != 200:
            raise ConnectorError(f"QuickBooks GET {path.split('?')[0]} failed (HTTP {r.status_code})")
        return r.json()

    def post(self, path: str, body: dict[str, Any], request_id: Optional[str] = None) -> dict[str, Any]:
        p = {"minorversion": MINOR_VERSION}
        if request_id:
            p["requestid"] = request_id[:50]          # Intuit de-duplicates retries carrying the same requestid
        r = self.http.post(self._url(path), params=p, json=body, headers={**self._headers(), "Content-Type": "application/json"})
        if r.status_code not in (200, 201):
            raise ConnectorError(f"QuickBooks POST {path} failed (HTTP {r.status_code})")
        return r.json()

    def query_all(self, entity: str, where: str = "", page: int = 1000, cap: int = 20000) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        start = 1
        while start <= cap:
            sql = f"select * from {entity}{(' where ' + where) if where else ''} startposition {start} maxresults {page}"
            body = self.get("query", {"query": sql})
            rows = (body.get("QueryResponse") or {}).get(entity, [])
            out += rows
            if len(rows) < page:
                break
            start += page
        return out


def run_local_callback(cfg: QboConfig, client: QboClient, open_browser: bool = True, timeout: int = 300) -> None:
    """One-time setup helper (CLI): prints the authorisation URL, waits for Intuit's redirect on the local
    redirect URI and stores the tokens. Use it when the API server is not running."""
    import http.server
    import webbrowser

    url, state = QboClient.authorize_url(cfg)
    parsed = urllib.parse.urlparse(cfg.redirect_uri)
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                        # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = q.get("state", [""])[0] == state and "code" in q
            if ok:
                result["code"], result["realm"] = q["code"][0], q.get("realmId", [""])[0]
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"QuickBooks connected. You can close this tab." if ok else b"Authorisation failed.")

        def log_message(self, *a):                               # keep codes out of logs
            pass

    server = http.server.HTTPServer((parsed.hostname or "localhost", parsed.port or 8000), Handler)
    server.timeout = timeout
    print("Open this URL, sign in to the QuickBooks SANDBOX company and approve:\n" + url)
    if open_browser:
        webbrowser.open(url)
    server.handle_request()
    server.server_close()
    if "code" not in result:
        raise ConnectorError("no valid authorisation callback received")
    client.exchange_code(result["code"], result["realm"])
    print("Stored. Put these in backend/.env if not there yet: QBO_REALM_ID=" + (client.cfg.realm_id or "<realm id>"))
