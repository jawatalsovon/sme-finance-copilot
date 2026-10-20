"""Approval queue for T2 (draft) actions.

The agent can only *request*; the owner decides through the API (POST /approvals/{id}/approve
or /reject). There is deliberately no agent tool that approves anything.

payload_hash = sha256(canonical payload JSON) is stored at request time, written to the audit
chain, and re-checked right before execution, so a payload edited in the database after the
owner looked at it is refused.
"""
from __future__ import annotations

import json
from typing import Any

from backend.core.ids import new_id
from backend.core.util import canonical_json, sha256_hex, to_json, utc_now_iso
from backend.ledger.db import Database
from backend.security.audit import AuditLog


class ApprovalError(Exception):
    pass


class ApprovalQueue:
    def __init__(self, db: Database, audit: AuditLog):
        self.db = db
        self.audit = audit

    def request(self, *, company_id: str, tool: str, tier: str, args: dict[str, Any],
                requested_by: str, title: str = "", session_id: str | None = None) -> dict:
        payload = {"company_id": company_id, "tool": tool, "args": args}
        payload_json = canonical_json(payload)
        payload_hash = sha256_hex(payload_json)
        approval_id = new_id("apr")
        now = utc_now_iso()
        self.db.execute(
            "INSERT INTO approvals (id, company_id, tool, tier, title, payload_json, payload_hash, status, "
            "requested_by, requested_at, session_id) VALUES (?,?,?,?,?,?,?, 'pending', ?,?,?)",
            [approval_id, company_id, tool, tier, title, payload_json, payload_hash, requested_by, now, session_id],
        )
        self.audit.append("approval_requested", actor=requested_by, company_id=company_id, tool=tool, tier=tier,
                          payload={"approval_id": approval_id, "payload_hash": payload_hash, "title": title})
        return self.get(approval_id)

    def get(self, approval_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM approvals WHERE id = ?", [approval_id])
        if not row:
            raise ApprovalError(f"approval {approval_id} not found")
        return self._public(row)

    def list(self, company_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM approvals WHERE 1=1"
        params: list = []
        if company_id:
            sql += " AND company_id = ?"
            params.append(company_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY requested_at DESC LIMIT ?"
        params.append(int(limit))
        return [self._public(r) for r in self.db.query_dicts(sql, params)]

    def verified_payload(self, approval_id: str) -> dict:
        """Payload of a pending approval after re-checking its hash."""
        row = self.db.query_one("SELECT * FROM approvals WHERE id = ?", [approval_id])
        if not row:
            raise ApprovalError(f"approval {approval_id} not found")
        if row["status"] != "pending":
            raise ApprovalError(f"approval {approval_id} is already {row['status']}")
        if sha256_hex(row["payload_json"]) != row["payload_hash"]:
            self.audit.append("approval_tamper_detected", actor="system", company_id=row["company_id"],
                              tool=row["tool"], tier=row["tier"], payload={"approval_id": approval_id})
            raise ApprovalError("approval payload was modified after it was requested; refusing to execute")
        return json.loads(row["payload_json"])

    def mark(self, approval_id: str, status: str, decided_by: str, note: str = "",
             result: dict | None = None) -> dict:
        if status not in ("approved", "rejected", "failed"):
            raise ValueError(status)
        row = self.db.query_one("SELECT status, company_id, tool, tier, payload_hash FROM approvals WHERE id = ?",
                                [approval_id])
        if not row:
            raise ApprovalError(f"approval {approval_id} not found")
        if row["status"] != "pending":
            raise ApprovalError(f"approval {approval_id} is already {row['status']}")
        self.db.execute(
            "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?, decision_note = ?, result_json = ? "
            "WHERE id = ?",
            [status, decided_by, utc_now_iso(), note, to_json(result) if result is not None else None, approval_id],
        )
        self.audit.append(f"approval_{status}", actor=decided_by, company_id=row["company_id"], tool=row["tool"],
                          tier=row["tier"], payload={"approval_id": approval_id, "payload_hash": row["payload_hash"],
                                                     "note": note, "result": result})
        return self.get(approval_id)

    @staticmethod
    def _public(row: dict) -> dict:
        out = dict(row)
        out["payload"] = json.loads(out.pop("payload_json"))
        rj = out.pop("result_json", None)
        out["result"] = json.loads(rj) if rj else None
        return out
