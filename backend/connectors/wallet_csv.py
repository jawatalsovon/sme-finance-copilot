"""Mobile-wallet / multi-currency account statement CSV (bKash-style, Wise-style) -> external_statement_lines."""
from __future__ import annotations

from backend.connectors._filebase import FileConnector
from backend.connectors.base import ConnectorMetadata, register


def _is_wallet(profile) -> bool:
    o = profile.options
    return profile.target == "statement" and (o.get("channel") == "wallet" or o.get("source_system") == "wallet_csv") \
        and o.get("layout", "movements") == "movements"


@register
class WalletCsvConnector(FileConnector):
    metadata = ConnectorMetadata(
        id="wallet_csv", name="Mobile wallet / multi-currency statement (CSV)", category="wallet",
        auth_type="file_upload", status="file",
        description="bKash-style merchant wallet statements (gross credit, fee deducted from balance) and Wise-style "
                    "multi-currency statements (one file per currency, own Currency column, fees, conversions). "
                    "Foreign-currency lines are converted to the base currency with the company's dated FX rates.",
        canonical_tables=["external_statement_lines"], modes=["file"])
    system_name = "wallet_csv"
    accept = staticmethod(_is_wallet)
    default_channel = "wallet"
