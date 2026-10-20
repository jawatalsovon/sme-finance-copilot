"""Bank statement CSV / Excel -> external_statement_lines (channel 'bank')."""
from __future__ import annotations

from backend.connectors._filebase import FileConnector
from backend.connectors.base import ConnectorMetadata, register


def _is_bank(profile) -> bool:
    o = profile.options
    return profile.target == "statement" and o.get("channel", "bank") == "bank" and \
        o.get("source_system", "bank_csv") == "bank_csv" and o.get("layout", "movements") == "movements"


@register
class BankCsvConnector(FileConnector):
    metadata = ConnectorMetadata(
        id="bank_csv", name="Bank statement (CSV / Excel)", category="bank", auth_type="file_upload", status="file",
        description="Reads a bank's downloadable statement (Withdrawal/Deposit, Debit/Credit or Money In/Out "
                    "layouts, junk header rows, any date format) into verified statement lines with a running balance. "
                    "This is the 'reality' side of Cash Truth. Unknown layouts get a proposed column mapping "
                    "the owner confirms once.",
        canonical_tables=["external_statement_lines"], modes=["file"])
    system_name = "bank_csv"
    accept = staticmethod(_is_bank)
    default_channel = "bank"
