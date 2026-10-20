"""Permission tiers.

    T0 Read            look at ledger rows, balances, evidence
    T1 Analyse/Suggest run engine analyses, propose (but not write) corrections
    T2 Draft           write DRAFTS only (ledger status='draft', connector sandbox drafts);
                       every T2 action goes to the owner's approval queue first
    T3 Limited execute low-risk actions that take effect without per-item approval
                       (e.g. mark a reminder draft "ready"); capped per company per day

A company is granted one tier; it may use every tool at or below it.
There is no tier that moves money, and the tool registry refuses to register one.
"""
from __future__ import annotations

import re
from enum import Enum


class Tier(str, Enum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"

    @property
    def level(self) -> int:
        return int(self.value[1])

    def allows(self, other: "Tier") -> bool:
        """True if a company granted `self` may use a tool of tier `other`."""
        return self.level >= other.level

    @classmethod
    def parse(cls, value: "str | Tier") -> "Tier":
        if isinstance(value, Tier):
            return value
        try:
            return cls(str(value).strip().upper())
        except ValueError:
            raise ValueError(f"unknown tier {value!r}; expected T0..T3") from None


TIER_LABELS = {
    Tier.T0: "Read",
    Tier.T1: "Analyse / Suggest",
    Tier.T2: "Draft (owner approval, sandbox/draft writes only)",
    Tier.T3: "Limited execute (capped per day)",
}


class MoneyMovementForbidden(Exception):
    """Raised when something tries to register a tool that could move money."""


# Names/descriptions that indicate money movement. Checked at tool registration.
_MONEY_WORDS = re.compile(
    r"\b(pay|payment_send|send_money|transfer|wire|withdraw|disburse|payout|remit|"
    r"refund_issue|charge_card|top_?up|cash_?out|settle_payment|initiate_payment|move_funds|"
    r"(initiate|make|send|execute|submit|authori[sz]e)\s+(an?\s+)?payments?|move\s+(money|funds)|"
    r"transfer\s+(money|funds)|apply\s+for\s+(a\s+)?(loan|credit))\b",
    re.I,
)


def looks_like_money_movement(name: str, description: str = "") -> bool:
    text = f"{name.replace('_', ' ')} {name} {description}"
    return bool(_MONEY_WORDS.search(text))
