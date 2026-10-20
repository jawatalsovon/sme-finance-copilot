"""Replays recorded-style QuickBooks Online sandbox responses from a folder of JSON files (offline demos and tests).

Folder layout: <dir>/<Entity>.json holding the list of entity objects, e.g. Invoice.json = [ {...}, ... ].
The transport answers: token refresh, `query?query=select * from <Entity> ... startposition N maxresults M`
(with paging), and POST /journalentry (idempotent on the `requestid` query parameter, as Intuit does).
It records every request in `.calls` so tests can assert what was (not) sent.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx


class FixtureTransport(httpx.MockTransport):
    def __init__(self, directory: str | Path, refresh_token: str = "rt-new"):
        self.dir = Path(directory)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.created: dict[str, dict[str, Any]] = {}        # requestid -> response body
        self.created_bodies: list[dict[str, Any]] = []
        self._next_id = 9000
        self._refresh_token = refresh_token
        super().__init__(self._handle)

    def _rows(self, entity: str) -> list[dict[str, Any]]:
        f = self.dir / f"{entity}.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []

    def _handle(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        params = dict(req.url.params)
        body = json.loads(req.content) if req.headers.get("content-type", "").startswith("application/json") and req.content else {}
        self.calls.append((req.method, req.url.path, {"params": params, "body": body}))
        if req.url.host == "oauth.platform.intuit.com":
            return httpx.Response(200, json={"access_token": "at-fixture", "refresh_token": self._refresh_token,
                                             "expires_in": 3600, "token_type": "bearer"})
        if req.method == "GET" and req.url.path.endswith("/query"):
            q = params.get("query", "")
            m = re.match(r"select \* from (\w+)(?: where (.*?))?(?: startposition (\d+))?(?: maxresults (\d+))?$", q.strip(), re.I)
            if not m:
                return httpx.Response(400, json={"Fault": {"Error": [{"Message": "unsupported query"}]}})
            rows = self._rows(m[1])
            start, size = int(m[3] or 1), int(m[4] or 1000)
            return httpx.Response(200, json={"QueryResponse": {m[1]: rows[start - 1:start - 1 + size]} if rows[start - 1:start - 1 + size] else {}})
        if req.method == "GET" and req.url.path.endswith("/preferences"):
            return httpx.Response(200, json={"Preferences": {"CurrencyPrefs": {"HomeCurrency": {"value": "USD"}}}})
        if req.method == "GET" and "/companyinfo/" in req.url.path:
            return httpx.Response(200, json={"CompanyInfo": {"CompanyName": "Sandbox Company (fixture)", "Country": "US"}})
        if req.method == "POST" and req.url.path.endswith("/journalentry"):
            rid = params.get("requestid")
            if rid and rid in self.created:
                return httpx.Response(200, json=self.created[rid])
            self._next_id += 1
            self.created_bodies.append(body)
            resp = {"JournalEntry": {**body, "Id": str(self._next_id), "SyncToken": "0",
                                      "MetaData": {"CreateTime": "2026-10-19T10:00:00-07:00"}}, "time": "2026-10-19T10:00:00.000-07:00"}
            if rid:
                self.created[rid] = resp
            return httpx.Response(200, json=resp)
        return httpx.Response(404, json={"Fault": {"Error": [{"Message": f"fixture has no route for {req.method} {url}"}]}})
