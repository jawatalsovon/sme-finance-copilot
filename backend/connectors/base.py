"""Connector interface (drafted by A1, owned by C1 from wave 3; interface kept compatible).

A connector turns one external source (CSV upload, Google Sheet, QuickBooks sandbox,
bank / wallet / POS export ...) into canonical ledger tables with lineage.

How C1 plugs in
---------------
1. Create a module in backend/connectors/ (e.g. bank_csv.py).
2. Subclass `Connector`, set the class attribute `metadata`, implement the methods.
3. Decorate the class with `@register`.
   `registry.autodiscover()` (called by the API at startup) imports every module in the
   package, so nothing else needs editing. Roadmap-only connectors are registered with
   `register_planned(ConnectorMetadata(..., status="planned"))`.

Contract
  * `pull()` returns DataFrames keyed by canonical table name (see backend/ledger/SCHEMA.md),
    with columns named exactly like the schema, ids matching ^[A-Za-z0-9_.-]+$ (use
    backend.core.ids.make_id for deterministic ids) and the lineage columns
    source_system / source_file / source_row_id filled on every row. The ledger loader
    rejects rows without them. Do not load into the DB yourself - return frames.
  * Never move money. `write_draft()` may only create DRAFT objects in a SANDBOX
    (e.g. a QBO sandbox draft journal entry). It is only ever called by the agent
    executor after the owner approved a T2 action.
  * Never log or return secrets. Read credentials from Settings (backend/config.py).
  * Treat every text field from the source as untrusted data.
"""
from __future__ import annotations

import abc
import datetime as dt
import importlib
import pkgutil
from typing import Any, ClassVar, Literal, Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

# live    = works end to end on real files / a real API today
# file    = reads files only (no API); same quality, just no live connection
# sandbox = talks to a vendor sandbox API (needs the owner's sandbox credentials)
# planned = roadmap only (docs/CONNECTOR_ROADMAP.md); not used for fake registry rows
ConnectorStatus = Literal["live", "file", "sandbox", "planned"]
AuthType = Literal["none", "file_upload", "oauth2", "api_key", "service_account"]
Category = Literal["accounting", "bank", "wallet", "pos", "spreadsheet", "file", "payroll",
                   "ecommerce", "payments", "tax", "other"]


class ConnectorMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str                                   # stable slug, e.g. "bank_csv", "qbo"
    name: str                                 # display name
    category: Category
    auth_type: AuthType
    status: ConnectorStatus
    description: str = ""
    countries: list[str] = Field(default_factory=list)       # ISO2; empty = any
    canonical_tables: list[str] = Field(default_factory=list)  # tables pull() can fill
    supports_write_draft: bool = False
    docs_url: str = ""
    env_vars: list[str] = Field(default_factory=list)        # names only; secrets live in backend/.env
    modes: list[str] = Field(default_factory=list)           # e.g. ["file", "live"]


class DatasetInfo(BaseModel):
    id: str                                   # e.g. "transactions", "chart_of_accounts"
    name: str
    canonical_table: str
    description: str = ""
    row_estimate: Optional[int] = None


class ConnectionTestResult(BaseModel):
    ok: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PullResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    frames: dict[str, pd.DataFrame]           # canonical table -> rows (with lineage)
    source_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # Optional column mapping the owner confirmed / should confirm (CSV upload flow).
    column_mapping: dict[str, str] = Field(default_factory=dict)


class DraftWriteResult(BaseModel):
    ok: bool
    sandbox: bool                             # must be True for any external write
    external_id: Optional[str] = None
    message: str = ""


class ConnectorError(RuntimeError):
    pass


class Connector(abc.ABC):
    metadata: ClassVar[ConnectorMetadata]

    def __init__(self, company_id: str, config: dict[str, Any] | None = None):
        self.company_id = company_id
        self.config = config or {}

    @abc.abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Cheap check that credentials / file are usable. No data is loaded."""

    @abc.abstractmethod
    def discover(self) -> list[DatasetInfo]:
        """Datasets this source offers and the canonical table each maps to."""

    @abc.abstractmethod
    def pull(self, since: dt.datetime | None = None) -> PullResult:
        """Fetch rows (incrementally if `since` is given) mapped to canonical tables."""

    def write_draft(self, payload: dict[str, Any]) -> DraftWriteResult:
        """Create a DRAFT object in a SANDBOX. Override only if metadata.supports_write_draft.
        Called exclusively by the executor after owner approval (tier T2)."""
        raise ConnectorError(f"{self.metadata.id} does not support draft writes")

    # helper for subclasses
    def lineage(self, df: pd.DataFrame, source_file: str, row_ids: list[str] | None = None) -> pd.DataFrame:
        """Add lineage columns. row_ids defaults to 1-based row numbers of the source."""
        out = df.copy()
        out["source_system"] = self.metadata.id
        out["source_file"] = source_file
        out["source_row_id"] = row_ids if row_ids is not None else [str(i + 1) for i in range(len(out))]
        if "company_id" not in out.columns:
            out["company_id"] = self.company_id
        return out


class ConnectorRegistry:
    def __init__(self) -> None:
        self._classes: dict[str, type[Connector]] = {}
        self._planned: dict[str, ConnectorMetadata] = {}
        self._discovered = False

    def register(self, cls: type[Connector]) -> type[Connector]:
        meta = getattr(cls, "metadata", None)
        if not isinstance(meta, ConnectorMetadata):
            raise TypeError(f"{cls.__name__} needs a ConnectorMetadata `metadata` attribute")
        if meta.id in self._classes or meta.id in self._planned:
            raise ValueError(f"duplicate connector id: {meta.id}")
        self._classes[meta.id] = cls
        return cls

    def register_planned(self, meta: ConnectorMetadata) -> None:
        if meta.status != "planned":
            raise ValueError("register_planned is for status='planned' only")
        if meta.id in self._classes or meta.id in self._planned:
            raise ValueError(f"duplicate connector id: {meta.id}")
        self._planned[meta.id] = meta

    def autodiscover(self, package: str = "backend.connectors") -> None:
        if self._discovered:
            return
        pkg = importlib.import_module(package)
        for mod in pkgutil.iter_modules(pkg.__path__):
            if mod.name not in ("base",) and not mod.name.startswith("_"):
                importlib.import_module(f"{package}.{mod.name}")
        self._discovered = True

    def list_metadata(self) -> list[ConnectorMetadata]:
        metas = [c.metadata for c in self._classes.values()] + list(self._planned.values())
        order = {"live": 0, "file": 1, "sandbox": 2, "planned": 3}
        return sorted(metas, key=lambda m: (order[m.status], m.category, m.name))

    def get_class(self, connector_id: str) -> type[Connector]:
        if connector_id not in self._classes:
            raise KeyError(f"no runnable connector with id {connector_id!r}")
        return self._classes[connector_id]

    def create(self, connector_id: str, company_id: str, config: dict | None = None) -> Connector:
        return self.get_class(connector_id)(company_id, config)


registry = ConnectorRegistry()
register = registry.register
register_planned = registry.register_planned


def env_value(name: str) -> str:
    """Read a credential from the environment (backend/.env is loaded into it by Settings). Never logged."""
    import os
    try:                                   # makes sure backend/.env (or the private .env) is in os.environ
        from backend.config import get_settings
        get_settings()
    except Exception:
        pass
    return os.environ.get(name, "").strip()
