"""PII redaction before any text reaches an LLM provider.

    session = Redactor(names=["Rahim Uddin"]).session()
    safe = session.redact("Call Rahim Uddin on +8801711000000")   # 'Call [NAME_1] on [PHONE_1]'
    session.restore(llm_answer)                                    # placeholders -> originals

The placeholder map lives only in server memory for the chat session; it is never sent to
the provider or written to the audit log. Same value -> same placeholder within a session,
so the model can still reason about "the same customer".

Detected: e-mail addresses, phone numbers (+international, Bangladeshi mobile, US style),
payment card numbers (Luhn-checked), IBANs, account numbers next to a keyword
(a/c, acct, account no ...), national ids / passport / TIN / SSN next to a keyword,
US SSN pattern, bare 10-18 digit runs (account/ID-like), and a configurable name list
(employee names are added automatically from the ledger).

Deliberately NOT redacted: money amounts, dates, ledger ids and evidence refs - the model
needs them and they are not personal data. Patterns are anchored so they do not fire on
decimals, dates or ids such as "jl-3f2a90123456".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

_B = r"(?<![\w.\-+])"   # left boundary: not inside a word, decimal, id or phone
_E = r"(?![\w.])"       # right boundary


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _luhn_ok(number: str) -> bool:
    d = [int(c) for c in _digits(number)]
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, x in enumerate(reversed(d)):
        if i % 2 == 1:
            x *= 2
            if x > 9:
                x -= 9
        total += x
    return total % 10 == 0


@dataclass
class _Rule:
    kind: str
    pattern: re.Pattern
    group: str | int = 0                        # which group to replace
    validate: Callable[[str], bool] | None = None


_RULES: list[_Rule] = [
    _Rule("EMAIL", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    _Rule("IBAN", re.compile(_B + r"[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?" + _E)),
    _Rule("CARD", re.compile(_B + r"(?:\d[ \-]?){12,18}\d" + _E), validate=_luhn_ok),
    _Rule("ACCOUNT", re.compile(
        r"(?i)\b(?:a/c|acct|account|acc|bank\s+account|wallet)\.?\s*(?:no\.?|number|num|#)?\s*[:#]?\s*"
        r"(?P<v>\d[\d \-]{4,22}\d)" + _E), group="v"),
    _Rule("NATIONAL_ID", re.compile(
        r"(?i)\b(?:nid|national\s+id(?:entity)?(?:\s+card)?|passport|tin|tax\s+id|ssn|e-?tin)\b\.?\s*"
        r"(?:no\.?|number|#)?\s*[:#]?\s*(?P<v>[A-Z0-9][A-Z0-9\-]{5,20})" + _E),
        group="v", validate=lambda v: len(_digits(v)) >= 5),
    _Rule("NATIONAL_ID", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),                     # US SSN shape
    _Rule("PHONE", re.compile(r"(?<![\w+])\+\d[\d \-().]{6,18}\d" + _E),
          validate=lambda v: 8 <= len(_digits(v)) <= 15),
    _Rule("PHONE", re.compile(_B + r"01[3-9]\d{2}[ \-]?\d{6}" + _E)),               # BD mobile
    _Rule("PHONE", re.compile(r"\(\d{3}\)\s?\d{3}-\d{4}\b")),                       # US style
    _Rule("ACCOUNT", re.compile(_B + r"\d{10,18}" + _E)),                           # bare long digit run
]


@dataclass
class RedactionSession:
    names: list[str] = field(default_factory=list)
    forward: dict[str, str] = field(default_factory=dict)   # original -> placeholder
    reverse: dict[str, str] = field(default_factory=dict)   # placeholder -> original
    counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        cleaned = sorted({n.strip() for n in self.names if n and len(n.strip()) >= 3}, key=len, reverse=True)
        self._name_re = (
            re.compile(r"(?i)(?<!\w)(" + "|".join(re.escape(n) for n in cleaned) + r")(?!\w)") if cleaned else None
        )

    def _placeholder(self, kind: str, original: str) -> str:
        key = f"{kind}\x00{original.lower() if kind in ('EMAIL', 'NAME') else original}"
        if key in self.forward:
            return self.forward[key]
        self.counts[kind] = self.counts.get(kind, 0) + 1
        ph = f"[{kind}_{self.counts[kind]}]"
        self.forward[key] = ph
        self.reverse[ph] = original
        return ph

    def redact(self, text: str) -> str:
        if not text:
            return text
        out = text
        for rule in _RULES:
            def repl(m: re.Match, rule: _Rule = rule) -> str:
                value = m.group(rule.group)
                if rule.validate and not rule.validate(value):
                    return m.group(0)
                ph = self._placeholder(rule.kind, value)
                if rule.group == 0:
                    return ph
                s, e = m.span(rule.group)
                base = m.start(0)
                whole = m.group(0)
                return whole[: s - base] + ph + whole[e - base:]
            out = rule.pattern.sub(repl, out)
        if self._name_re:
            out = self._name_re.sub(lambda m: self._placeholder("NAME", m.group(1)), out)
        return out

    def redact_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {k: self.redact_obj(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.redact_obj(v) for v in obj]
        return obj

    def restore(self, text: str) -> str:
        if not text or not self.reverse:
            return text
        return re.sub(r"\[[A-Z_]+_\d+\]", lambda m: self.reverse.get(m.group(0), m.group(0)), text)

    def restore_obj(self, obj: Any) -> Any:
        """Restore placeholders inside model-produced structures (e.g. tool arguments)."""
        if isinstance(obj, str):
            return self.restore(obj)
        if isinstance(obj, dict):
            return {k: self.restore_obj(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.restore_obj(v) for v in obj]
        return obj

    @property
    def redaction_count(self) -> int:
        return len(self.reverse)


class Redactor:
    def __init__(self, names: list[str] | None = None):
        self.names = list(names or [])

    def session(self, extra_names: list[str] | None = None) -> RedactionSession:
        return RedactionSession(names=self.names + list(extra_names or []))

    def redact(self, text: str) -> str:
        """One-off redaction without keeping the map (e.g. for logs)."""
        return self.session().redact(text)
