"""Append-only audit log with a SHA-256 hash chain.

Each row stores prev_hash (the previous row's row_hash) and its own
row_hash = sha256(canonical JSON of {seq, ts, company_id, actor, action, tool, tier,
payload_json, payload_hash, prev_hash}). `verify_chain()` recomputes everything and
reports any edited, deleted, inserted or reordered row. The current head is also
written to audit_head so deleting rows from the end is detected too.

Only `append()` writes; there is no update/delete API in the code base.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.core.util import canonical_json, sha256_hex, utc_now_iso
from backend.ledger.db import Database

GENESIS_HASH = "0" * 64
_HASHED_FIELDS = ("seq", "ts", "company_id", "actor", "action", "tool", "tier",
                  "payload_json", "payload_hash", "prev_hash")


def compute_row_hash(row: dict) -> str:
    return sha256_hex(canonical_json({k: row.get(k) for k in _HASHED_FIELDS}))


@dataclass
class VerifyResult:
    ok: bool
    checked: int
    head_seq: int | None
    errors: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "checked": self.checked, "head_seq": self.head_seq, "errors": self.errors}


class AuditLog:
    def __init__(self, db: Database):
        self.db = db

    def append(
        self,
        action: str,
        *,
        actor: str,
        company_id: str | None = None,
        tool: str | None = None,
        tier: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict:
        payload_json = canonical_json(payload or {})
        with self.db.locked() as conn:
            last = conn.execute("SELECT seq, row_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
            seq = (last[0] + 1) if last else 1
            prev_hash = last[1] if last else GENESIS_HASH
            row = {
                "seq": seq, "ts": utc_now_iso(), "company_id": company_id, "actor": actor,
                "action": action, "tool": tool, "tier": tier, "payload_json": payload_json,
                "payload_hash": sha256_hex(payload_json), "prev_hash": prev_hash,
            }
            row["row_hash"] = compute_row_hash(row)
            conn.execute(
                "INSERT INTO audit_log (seq, ts, company_id, actor, action, tool, tier, payload_json, "
                "payload_hash, prev_hash, row_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [row[k] for k in ("seq", "ts", "company_id", "actor", "action", "tool", "tier",
                                  "payload_json", "payload_hash", "prev_hash", "row_hash")],
            )
            conn.execute(
                "INSERT OR REPLACE INTO audit_head (id, seq, row_hash, updated_at) VALUES (1, ?, ?, ?)",
                [seq, row["row_hash"], row["ts"]],
            )
        return row

    def list(self, company_id: str | None = None, limit: int = 100, offset: int = 0,
             action: str | None = None) -> list[dict]:
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params: list = []
        if company_id:
            sql += " AND company_id = ?"
            params.append(company_id)
        if action:
            sql += " AND action = ?"
            params.append(action)
        sql += " ORDER BY seq DESC LIMIT ? OFFSET ?"
        params += [int(limit), int(offset)]
        return self.db.query_dicts(sql, params)

    def count_today(self, company_id: str, action: str, tool: str) -> int:
        day = utc_now_iso()[:10]
        return int(self.db.scalar(
            "SELECT COUNT(*) FROM audit_log WHERE company_id = ? AND action = ? AND tool = ? AND ts LIKE ?",
            [company_id, action, tool, f"{day}%"],
        ) or 0)

    def verify_chain(self) -> VerifyResult:
        rows = self.db.query_dicts("SELECT * FROM audit_log ORDER BY seq")
        errors: list[dict] = []
        prev_hash = GENESIS_HASH
        expected_seq = 1
        for r in rows:
            seq = r["seq"]
            if seq != expected_seq:
                errors.append({"seq": seq, "error": f"sequence gap or reorder: expected {expected_seq}"})
                expected_seq = seq
            if r["prev_hash"] != prev_hash:
                errors.append({"seq": seq, "error": "prev_hash does not match previous row (row deleted, inserted or reordered)"})
            if sha256_hex(r["payload_json"]) != r["payload_hash"]:
                errors.append({"seq": seq, "error": "payload_hash mismatch (payload edited)"})
            if compute_row_hash(r) != r["row_hash"]:
                errors.append({"seq": seq, "error": "row_hash mismatch (row edited)"})
            prev_hash = r["row_hash"]
            expected_seq += 1

        head = self.db.query_one("SELECT seq, row_hash FROM audit_head WHERE id = 1")
        last_seq = rows[-1]["seq"] if rows else 0
        if head is None and rows:
            errors.append({"seq": None, "error": "audit_head missing"})
        elif head is not None:
            if head["seq"] != last_seq or head["row_hash"] != (rows[-1]["row_hash"] if rows else None):
                errors.append({"seq": head["seq"], "error": f"chain head mismatch: head says seq {head['seq']}, "
                                                            f"log ends at {last_seq} (rows truncated or appended outside the API)"})
        return VerifyResult(ok=not errors, checked=len(rows), head_seq=last_seq or None, errors=errors)
