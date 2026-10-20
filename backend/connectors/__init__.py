"""Connectors (owner: C1). Contract: base.py."""
from backend.connectors.base import (
    ConnectionTestResult,
    Connector,
    ConnectorError,
    ConnectorMetadata,
    ConnectorRegistry,
    DatasetInfo,
    DraftWriteResult,
    PullResult,
    register,
    register_planned,
    registry,
)

__all__ = [
    "ConnectionTestResult", "Connector", "ConnectorError", "ConnectorMetadata", "ConnectorRegistry",
    "DatasetInfo", "DraftWriteResult", "PullResult", "register", "register_planned", "registry",
]
