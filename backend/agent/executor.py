"""Tool executor: the single choke point between the model and anything that runs.

For every call:
  1. unknown tool / invalid args            -> denied (audited)
  2. tool tier above the company's grant    -> denied (audited)
  3. T2 (draft)                             -> precheck, then queued in the approval queue;
                                               NOT executed (audited)
  4. T3 (limited execute)                   -> executed only while under the daily cap
  5. T0/T1/T3                               -> executed; result scanned by the injection
                                               guard; audited with a hash of the result
Owner approval later calls `execute_approved()`, which re-verifies the payload hash and
the company's current tier before running the T2 handler.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.agent.tools import ToolContext, ToolRegistry, ToolResult, validate_args
from backend.core.util import canonical_json, sha256_hex
from backend.ledger.db import Database
from backend.security.approvals import ApprovalError, ApprovalQueue
from backend.security.audit import AuditLog
from backend.security.injection import scan_value
from backend.security.permissions import PermissionStore
from backend.security.tiers import Tier

Status = Literal["executed", "queued_for_approval", "denied", "error"]


@dataclass
class ExecutionOutcome:
    status: Status
    tool: str
    tier: str | None
    result: ToolResult | None = None
    approval_id: str | None = None
    error: str | None = None
    injection_flags: list[dict] = field(default_factory=list)
    granted_tier: str | None = None           # the company's tier at decision time
    reason: str = ""                          # why: executed / queued / denied (plain words)

    def tier_decision(self) -> dict:
        """Machine-readable permission decision (used by /chat/sync and the audit log)."""
        decision = {"executed": "executed", "error": "executed", "queued_for_approval": "queued_for_approval",
                    "denied": "denied"}[self.status]
        return {"tool": self.tool, "tool_tier": self.tier, "granted_tier": self.granted_tier,
                "decision": decision, "reason": self.reason or self.error or ""}

    def for_model(self) -> dict:
        """What the LLM sees (before redaction/wrapping)."""
        out: dict[str, Any] = {"status": self.status, "tool": self.tool, "tier": self.tier}
        if self.result is not None:
            out.update(self.result.model_dump(mode="json"))
            out["evidence"] = out.get("evidence", [])[:50]
        if self.approval_id:
            out["approval_id"] = self.approval_id
            out["summary"] = ("Queued for the owner's approval. Nothing has been written. "
                              "Tell the owner it is waiting in the approvals inbox.")
        if self.error:
            out["error"] = self.error
        return out

    def for_client(self) -> dict:
        d = self.for_model()
        d["injection_flags"] = self.injection_flags
        return d


class ToolExecutor:
    def __init__(self, db: Database, registry: ToolRegistry, permissions: PermissionStore,
                 approvals: ApprovalQueue, audit: AuditLog, *, t3_daily_cap: int = 20,
                 settings: Any = None, connector_registry: Any = None, engine_factory=None):
        self.db = db
        self.registry = registry
        self.permissions = permissions
        self.approvals = approvals
        self.audit = audit
        self.t3_daily_cap = t3_daily_cap
        self.settings = settings
        self.connector_registry = connector_registry
        self.engine_factory = engine_factory

    def _ctx(self, company_id: str, actor: str, session_id: str | None, approval_id: str | None = None) -> ToolContext:
        return ToolContext(company_id=company_id, db=self.db, actor=actor, session_id=session_id,
                           approval_id=approval_id, settings=self.settings,
                           connector_registry=self.connector_registry, engine_factory=self.engine_factory)

    def _deny(self, company_id: str, tool: str, tier: str | None, actor: str, reason: str, args: Any,
              granted: str | None = None, session_id: str | None = None) -> ExecutionOutcome:
        self.audit.append("tool_denied", actor=actor, company_id=company_id, tool=tool, tier=tier,
                          payload={"reason": reason, "granted_tier": granted, "session_id": session_id,
                                   "args_hash": sha256_hex(canonical_json(args))})
        return ExecutionOutcome("denied", tool, tier, error=reason, granted_tier=granted, reason=reason)

    def execute(self, company_id: str, tool_name: str, args: dict, *, actor: str = "agent",
                session_id: str | None = None) -> ExecutionOutcome:
        granted = self.permissions.get_tier(company_id)
        g = granted.value
        spec = self.registry.get(tool_name)
        if spec is None:
            return self._deny(company_id, tool_name, None, actor,
                              f"unknown tool {tool_name!r} (no such capability exists)", args, g, session_id)
        tier = spec.tier
        if not isinstance(args, dict):
            return self._deny(company_id, tool_name, tier.value, actor, "arguments must be an object", args, g,
                              session_id)
        errs = validate_args(spec.parameters, args)
        if errs:
            return self._deny(company_id, tool_name, tier.value, actor, "invalid arguments: " + "; ".join(errs),
                              args, g, session_id)

        if not granted.allows(tier):
            return self._deny(company_id, tool_name, tier.value, actor,
                              f"tool needs tier {tier.value} but this company granted {g}", args, g, session_id)

        ctx = self._ctx(company_id, actor, session_id)
        if spec.precheck:  # e.g. balanced entry, ids belong to THIS company
            problem = spec.precheck(ctx, args)
            if problem:
                return self._deny(company_id, tool_name, tier.value, actor, f"rejected: {problem}", args, g,
                                  session_id)

        if tier == Tier.T2:
            title = spec.title(args) if spec.title else tool_name
            appr = self.approvals.request(company_id=company_id, tool=tool_name, tier=tier.value, args=args,
                                          requested_by=actor, title=title, session_id=session_id)
            return ExecutionOutcome("queued_for_approval", tool_name, tier.value, approval_id=appr["id"],
                                    granted_tier=g, reason="T2 draft: waiting for the owner's approval")

        if tier == Tier.T3:
            cap = spec.daily_cap if spec.daily_cap is not None else self.t3_daily_cap
            used = self.audit.count_today(company_id, "tool_executed", tool_name)
            if used >= cap:
                return self._deny(company_id, tool_name, tier.value, actor,
                                  f"daily cap reached ({used}/{cap}) for {tool_name}", args, g, session_id)

        out = self._run(spec, ctx, args)
        out.granted_tier = g
        out.reason = out.reason or f"{tier.value} tool allowed at granted tier {g}"
        return out

    def _run(self, spec, ctx: ToolContext, args: dict, approval_id: str | None = None) -> ExecutionOutcome:
        try:
            result = spec.handler(ctx, args)
        except Exception as e:  # tool bug or bad data: report, never crash the loop
            self.audit.append("tool_error", actor=ctx.actor, company_id=ctx.company_id, tool=spec.name,
                              tier=spec.tier.value, payload={"error": f"{type(e).__name__}: {e}",
                                                             "trace": traceback.format_exc(limit=3)[-800:],
                                                             "approval_id": approval_id})
            return ExecutionOutcome("error", spec.name, spec.tier.value,
                                    result=ToolResult.fail("tool_error", f"{type(e).__name__}: {e}"),
                                    error=f"{type(e).__name__}: {e}")
        flags = scan_value(result.data) + scan_value(result.summary, "$.summary")
        self.audit.append("tool_executed", actor=ctx.actor, company_id=ctx.company_id, tool=spec.name,
                          tier=spec.tier.value,
                          payload={"args": args, "ok": result.ok, "error_code": result.error_code,
                                   "result_hash": sha256_hex(canonical_json(result.model_dump(mode="json"))),
                                   "evidence_count": result.evidence_count, "approval_id": approval_id,
                                   "session_id": ctx.session_id})
        if flags:
            self.audit.append("injection_flagged", actor="system", company_id=ctx.company_id, tool=spec.name,
                              tier=spec.tier.value,
                              payload={"flags": [{"path": f["path"], "patterns": f["patterns"]} for f in flags[:20]],
                                       "count": len(flags), "session_id": ctx.session_id})
        status: Status = "executed" if result.ok or result.error_code != "tool_error" else "error"
        return ExecutionOutcome(status, spec.name, spec.tier.value, result=result, injection_flags=flags)

    def execute_approved(self, approval_id: str, approver: str) -> dict:
        """Owner approved a queued T2 action: verify, run, and record the outcome."""
        check_approver(approver)
        req = self.approvals.get(approval_id)
        if approver == req["requested_by"]:
            self._refuse_self_approval(req, approver)
        payload = self.approvals.verified_payload(approval_id)
        company_id, tool_name, args = payload["company_id"], payload["tool"], payload["args"]
        spec = self.registry.get(tool_name)
        if spec is None or spec.tier != Tier.T2:
            self.approvals.mark(approval_id, "failed", approver, note="tool no longer available as T2")
            raise ApprovalError("tool no longer available")
        granted = self.permissions.get_tier(company_id)
        if not granted.allows(spec.tier):
            self.approvals.mark(approval_id, "failed", approver,
                                note=f"company tier is now {granted.value}; T2 required")
            raise ApprovalError(f"company tier is now {granted.value}; cannot execute a T2 action")
        ctx = self._ctx(company_id, approver, None, approval_id=approval_id)
        outcome = self._run(spec, ctx, args, approval_id=approval_id)
        result = outcome.result.model_dump(mode="json") if outcome.result else None
        status = "approved" if outcome.status == "executed" and outcome.result and outcome.result.ok else "failed"
        return self.approvals.mark(approval_id, status, approver, result=result)

    def reject(self, approval_id: str, approver: str, note: str = "") -> dict:
        # No hash check: rejecting (even a tampered item) is always safe. mark() refuses decided items.
        check_approver(approver)
        return self.approvals.mark(approval_id, "rejected", approver, note=note)

    def _refuse_self_approval(self, req: dict, approver: str) -> None:
        self.audit.append("self_approval_refused", actor=approver, company_id=req["company_id"], tool=req["tool"],
                          tier=req["tier"], payload={"approval_id": req["id"]})
        raise ApprovalError("the requester of an action cannot approve it")


AGENT_ACTOR_PREFIXES = ("agent", "system", "llm", "assistant", "copilot")


class SelfApprovalForbidden(ApprovalError):
    pass


def check_approver(approver: str) -> None:
    """Approvals are an owner action. The agent (or anything posing as it) can never decide."""
    a = (approver or "").strip().lower()
    if not a or a.startswith(AGENT_ACTOR_PREFIXES):
        raise SelfApprovalForbidden(f"actor {approver!r} may not approve or reject; only the owner can")
