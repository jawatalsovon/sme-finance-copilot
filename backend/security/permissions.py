"""Per-company granted tier (set by the owner, audited)."""
from __future__ import annotations

from backend.core.util import utc_now_iso
from backend.ledger.db import Database
from backend.security.audit import AuditLog
from backend.security.tiers import Tier


class PermissionStore:
    def __init__(self, db: Database, audit: AuditLog, default_tier: str = "T1"):
        self.db = db
        self.audit = audit
        self.default_tier = Tier.parse(default_tier)

    def get_tier(self, company_id: str) -> Tier:
        val = self.db.scalar("SELECT granted_tier FROM company_permissions WHERE company_id = ?", [company_id])
        return Tier.parse(val) if val else self.default_tier

    def set_tier(self, company_id: str, tier: str | Tier, actor: str) -> Tier:
        new = Tier.parse(tier)
        old = self.get_tier(company_id)
        self.db.execute(
            "INSERT OR REPLACE INTO company_permissions (company_id, granted_tier, updated_by, updated_at) "
            "VALUES (?,?,?,?)",
            [company_id, new.value, actor, utc_now_iso()],
        )
        self.audit.append("permission_changed", actor=actor, company_id=company_id,
                          payload={"from": old.value, "to": new.value})
        return new
