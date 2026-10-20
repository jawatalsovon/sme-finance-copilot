"""Agent loop: model <-> tools, streamed as events.

    for event in agent.run(company_id, "What is my cash position?", session_id):
        ...  # dicts, see EVENT TYPES below

EVENT TYPES (contract for the API's SSE stream and the frontend)
  {"type": "session", "session_id", "company_id", "tier", "provider", "model"}
  {"type": "step", "kind": "llm_call", "step", "input_tokens", "output_tokens", "cost_usd", "tool_calls": [names]}
  {"type": "step", "kind": "tool_call", "step", "tool", "tier", "args"}
  {"type": "step", "kind": "tool_result", "step", "tool", "status", "summary", "evidence", "evidence_count",
       "approval_id"?, "error"?, "data"}
       "tier_decision": {"tool", "tool_tier", "granted_tier", "decision", "reason"}
  {"type": "step", "kind": "guard", "step", "tool" | None, "source"?: "user_message",
       "flags": [{"path", "patterns", "excerpt"}]}
  {"type": "step", "kind": "policy", "step": 0, "policy": "money_movement", "message"}
  {"type": "final", "answer", "evidence_ids", "invalid_evidence_ids", "unverified_numbers",
       "approvals": [ids], "refusals": ["money_movement"]?, "session_id", "steps"}
  {"type": "error", "code": "budget_exceeded" | "provider_error" | "unknown_company" | "max_steps", "message"}

Data flow per turn: owner text -> redact -> provider; tool output -> redact -> injection
envelope -> provider; model answer -> restore placeholders -> owner. The provider never sees
the placeholder map.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

from backend.agent.executor import ToolExecutor
from backend.agent.providers.base import Message, ProviderError
from backend.agent.providers.cost import BudgetExceeded, GuardedProvider
from backend.agent.tools import ToolRegistry
from backend.core.ids import new_id
from backend.core.util import business_today, sha256_hex, to_json, utc_now
from backend.ledger import queries
from backend.ledger.db import Database
from backend.ledger.lineage import extract_refs
from backend.security.audit import AuditLog
from backend.core.ids import reseed
from backend.security.injection import scan_text, wrap_untrusted
from backend.security.policy import INJECTION_POLICY_NOTE, MONEY_POLICY_NOTE, is_money_movement_request
from backend.security.permissions import PermissionStore
from backend.security.redaction import RedactionSession, Redactor
from backend.security.tiers import TIER_LABELS

SYSTEM_PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "system.md"
_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![\w])")
_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
_PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")


def _norm_number(s: str) -> str | None:
    try:
        d = Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None
    return format(d.normalize(), "f")


def numbers_in(text: str) -> set[str]:
    out = set()
    for m in _NUM.finditer(text or ""):
        n = _norm_number(m.group(0))
        if n is not None:
            out.add(n)
            if n.startswith("-"):
                out.add(n[1:])
    return out


def unverified_numbers(answer: str, sources: list[str]) -> list[str]:
    """Numbers in the answer that appear in no tool output (or user/system text).
    Small integers (< 100) and years are ignored. A non-empty list means the model may have
    calculated or invented a figure - shown to the owner and logged."""
    text = _PLACEHOLDER.sub(" ", _DATE.sub(" ", answer or ""))
    for ref in extract_refs(text):
        text = text.replace(ref, " ")
    allowed: set[str] = set()
    for s in sources:
        allowed |= numbers_in(_DATE.sub(" ", s))
    bad = []
    for n in sorted(numbers_in(text)):
        try:
            v = abs(Decimal(n))
        except InvalidOperation:
            continue
        if v < 100 and v == v.to_integral_value():
            continue
        if 1900 <= v <= 2100 and v == v.to_integral_value():
            continue
        if n not in allowed and n.lstrip("-") not in allowed:
            bad.append(n)
    return bad


@dataclass
class ChatSession:
    id: str
    company_id: str
    redaction: RedactionSession
    history: list[Message] = field(default_factory=list)   # redacted, as sent to the provider
    created_at: dt.datetime = field(default_factory=utc_now)


class SessionStore:
    """In-memory chat sessions (demo scope; lost on restart)."""

    def __init__(self, max_sessions: int = 200):
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()
        self.max_sessions = max_sessions

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def get_or_create(self, session_id: str | None, company_id: str, redactor: Redactor,
                      extra_names: list[str]) -> ChatSession:
        with self._lock:
            s = self._sessions.get(session_id or "")
            if s and s.company_id == company_id:
                return s
            if len(self._sessions) >= self.max_sessions:
                oldest = min(self._sessions.values(), key=lambda x: x.created_at)
                self._sessions.pop(oldest.id, None)
            s = ChatSession(id=new_id("chat"), company_id=company_id, redaction=redactor.session(extra_names))
            self._sessions[s.id] = s
            return s


class Agent:
    def __init__(self, db: Database, provider: GuardedProvider, registry: ToolRegistry, executor: ToolExecutor,
                 permissions: PermissionStore, audit: AuditLog, redactor: Redactor, *,
                 sessions: SessionStore | None = None, max_steps: int = 6, max_tokens: int = 1200):
        self.db = db
        self.provider = provider
        self.registry = registry
        self.executor = executor
        self.permissions = permissions
        self.audit = audit
        self.redactor = redactor
        self.sessions = sessions or SessionStore()
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.system_template = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")

    def system_prompt(self, company: dict, tier) -> str:
        return self.system_template.format(
            company_name=company["name"], company_id=company["id"], country=company["country"],
            base_currency=company["base_currency"], today=business_today().isoformat(),
            tier=tier.value, tier_label=TIER_LABELS[tier],
        )

    def run(self, company_id: str, user_message: str, session_id: str | None = None,
            actor: str = "owner", seed: int | str | None = None) -> Iterator[dict]:
        """Run one owner turn. With `seed`, ids are drawn deterministically (seed + audit length),
        so the same seed on the same starting state (e.g. after POST /admin/reset) gives the same
        session id, approval ids and - with the mock provider - the same answer."""
        if seed is not None:
            reseed(f"{seed}|{self.db.scalar('SELECT COUNT(*) FROM audit_log')}")
        company = queries.get_company(self.db, company_id)
        if not company:
            yield {"type": "error", "code": "unknown_company", "message": f"unknown company {company_id!r}"}
            return
        session = self.sessions.get_or_create(session_id, company_id, self.redactor,
                                              queries.employee_names(self.db, company_id))
        tier = self.permissions.get_tier(company_id)
        yield {"type": "session", "session_id": session.id, "company_id": company_id, "tier": tier.value,
               "provider": self.provider.name, "model": self.provider.model}
        self.audit.append("chat_message", actor=actor, company_id=company_id,
                          payload={"session_id": session.id, "message_sha256": sha256_hex(user_message),
                                   "chars": len(user_message)})

        # Server-side policy on the owner's own text, before the model sees it.
        policy_notes: list[Message] = []
        refusals: list[str] = []
        if is_money_movement_request(user_message):
            refusals.append("money_movement")
            policy_notes.append(Message("system", MONEY_POLICY_NOTE))
            self.audit.append("money_movement_refused", actor="system", company_id=company_id,
                              payload={"session_id": session.id, "message_sha256": sha256_hex(user_message),
                                       "reason": "no tool can move money at any tier"})
            yield {"type": "step", "kind": "policy", "step": 0, "policy": "money_movement",
                   "message": "Money movement is not possible at any tier; the request is refused and logged."}
        user_flags = scan_text(user_message)
        if user_flags:
            policy_notes.append(Message("system", INJECTION_POLICY_NOTE))
            flag = {"path": "$.user_message", "patterns": user_flags, "excerpt": user_message[:120]}
            self.audit.append("injection_flagged", actor="system", company_id=company_id,
                              payload={"source": "user_message", "flags": [{"path": flag["path"],
                                                                            "patterns": user_flags}],
                                       "count": 1, "session_id": session.id})
            yield {"type": "step", "kind": "guard", "step": 0, "tool": None, "source": "user_message",
                   "flags": [flag]}

        session.history.append(Message("user", session.redaction.redact(user_message)))
        self._trim(session)
        system = Message("system", self.system_prompt(company, tier))
        tools = self.registry.schemas(max_tier=tier)

        evidence_seen: list[str] = []
        approvals: list[str] = []
        raw_sources: list[str] = [user_message]

        for step in range(1, self.max_steps + 1):
            try:
                resp = self.provider.complete([system] + policy_notes + session.history, tools,
                                              max_tokens=self.max_tokens, company_id=company_id,
                                              session_id=session.id)
            except BudgetExceeded as e:
                yield {"type": "error", "code": "budget_exceeded", "message": str(e)}
                return
            except ProviderError as e:
                self.audit.append("llm_error", actor="system", company_id=company_id,
                                  payload={"error": str(e), "session_id": session.id})
                yield {"type": "error", "code": "provider_error", "message": str(e)}
                return
            yield {"type": "step", "kind": "llm_call", "step": step, "input_tokens": resp.usage.input_tokens,
                   "output_tokens": resp.usage.output_tokens, "cost_usd": round(resp.usage.cost_usd, 6),
                   "tool_calls": [c.name for c in resp.tool_calls]}

            if not resp.tool_calls:
                answer = session.redaction.restore(resp.text or "")
                session.history.append(Message("assistant", resp.text or ""))
                cited = extract_refs(answer)
                valid = [r for r in cited if r in evidence_seen]
                invalid = [r for r in cited if r not in evidence_seen]
                unverified = unverified_numbers(answer, raw_sources)
                self.audit.append("chat_answer", actor="agent", company_id=company_id,
                                  payload={"session_id": session.id, "answer_sha256": sha256_hex(answer),
                                           "evidence_ids": valid, "invalid_evidence_ids": invalid,
                                           "unverified_numbers": unverified, "approvals": approvals,
                                           "refusals": refusals, "steps": step})
                yield {"type": "final", "answer": answer, "evidence_ids": valid, "invalid_evidence_ids": invalid,
                       "unverified_numbers": unverified, "approvals": approvals, "refusals": refusals,
                       "session_id": session.id, "steps": step}
                return

            session.history.append(Message("assistant", resp.text or "", tool_calls=list(resp.tool_calls)))
            for call in resp.tool_calls:
                spec = self.registry.get(call.name)
                # The model only knows placeholders; tools run on the real values.
                real_args = session.redaction.restore_obj(call.arguments)
                yield {"type": "step", "kind": "tool_call", "step": step, "tool": call.name,
                       "tier": spec.tier.value if spec else None, "args": real_args}
                outcome = self.executor.execute(company_id, call.name, real_args, actor="agent",
                                                session_id=session.id)
                if outcome.result is not None:
                    evidence_seen += [r for r in outcome.result.evidence if r not in evidence_seen]
                if outcome.approval_id:
                    approvals.append(outcome.approval_id)
                model_view = outcome.for_model()
                raw_json = to_json(model_view)
                raw_sources.append(raw_json)
                client = outcome.for_client()
                yield {"type": "step", "kind": "tool_result", "step": step, "tool": call.name,
                       "status": outcome.status, "summary": client.get("summary", ""),
                       "evidence": client.get("evidence", []), "evidence_count": client.get("evidence_count", 0),
                       "approval_id": outcome.approval_id, "error": client.get("error"), "data": client.get("data"),
                       "tier_decision": outcome.tier_decision()}
                if outcome.injection_flags:
                    yield {"type": "step", "kind": "guard", "step": step, "tool": call.name,
                           "flags": outcome.injection_flags}
                content = wrap_untrusted(session.redaction.redact(raw_json), call.name, outcome.injection_flags)
                session.history.append(Message("tool", content, tool_call_id=call.id, name=call.name))

        msg = f"I stopped after {self.max_steps} steps without a final answer. Please narrow the question."
        self.audit.append("chat_answer", actor="agent", company_id=company_id,
                          payload={"session_id": session.id, "max_steps": True, "approvals": approvals})
        yield {"type": "error", "code": "max_steps", "message": msg}

    @staticmethod
    def _trim(session: ChatSession, max_messages: int = 40) -> None:
        """Drop the oldest whole turns (from a user message to the next) so tool calls stay paired."""
        h = session.history
        while len(h) > max_messages:
            nxt = next((i for i in range(1, len(h)) if h[i].role == "user"), None)
            if nxt is None:
                break
            del h[:nxt]
