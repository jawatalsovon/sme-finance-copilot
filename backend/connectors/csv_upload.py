"""Generic CSV / Excel upload with column-mapping profiles.

Statement-type files (bank, wallet, cash count, marketplace/POS payouts) go through the shared statement builder;
ledger-type files (journal exports, QuickBooks-style invoice/bill/chart-of-accounts CSVs, cash-book notebooks,
POS daily summaries) through backend.connectors._ledger. Which reader a file gets is decided by its mapping profile
(see _mapping.py): a built-in vendor profile, a profile the owner confirmed earlier, or a freshly proposed mapping.

Extra config keys (on top of those in _filebase): accounts (DataFrame / rows / Database with the chart of accounts,
used to resolve account codes and names).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd

from backend.connectors._accounts import AccountCatalog
from backend.connectors._filebase import FileConnector
from backend.connectors._ledger import READERS, LedgerCtx
from backend.connectors._mapping import MappingProfile
from backend.connectors._stmt import build_statement_lines
from backend.connectors._util import mask_account, read_sheet_names, read_table
from backend.connectors.base import (ConnectionTestResult, ConnectorError, ConnectorMetadata, DatasetInfo, PullResult,
                                     register)

TABLE_ORDER = ["accounts", "counterparties", "journal_entries", "journal_lines", "invoices", "bills",
               "external_statement_lines", "pos_daily_imports"]
LOAD_FIRST = ("coa", "parties")                # chart of accounts and parties before anything that needs them


def _units(path: Path, sheet: str | None) -> list[tuple[str | None, pd.DataFrame]]:
    """(sheet, table) for each readable sheet of a workbook, or the single table of a CSV."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        names = [sheet] if sheet else read_sheet_names(path)
        return [(n, read_table(path, n)[0]) for n in names]
    return [(None, read_table(path)[0])]


@register
class CsvUploadConnector(FileConnector):
    metadata = ConnectorMetadata(
        id="csv_upload", name="CSV / Excel upload (any layout)", category="file", auth_type="file_upload",
        status="file",
        description="Drop in any CSV or Excel file. Known layouts (vendor profiles) load straight away; a new layout "
                    "gets a proposed column mapping (header rules first, optional AI suggestion) that the owner "
                    "confirms once and the copilot remembers. Handles messy dates, thousand separators, "
                    "BDT/CNY/GBP, debit/credit or signed amounts and junk header rows. Every row keeps its file and row number.",
        canonical_tables=["external_statement_lines", "journal_entries", "journal_lines", "accounts",
                          "counterparties", "invoices", "bills", "pos_daily_imports"],
        modes=["file"])
    default_channel = "bank"

    # ------------------------------------------------------------------ classification
    def classify(self, df: pd.DataFrame, name: str, guess: bool = True) -> tuple[MappingProfile | None, dict, dict, str, list[str]]:
        """-> (profile|None, columns, options, label, warnings). Falls back to the statement heuristic only when
        `guess` (a lone sheet / CSV): tabs of a multi-sheet workbook with no known layout are skipped, not guessed."""
        headers = [c for c in df.columns if not str(c).startswith("_")]
        if self.config.get("mapping"):
            m = self.config["mapping"]
            prof = MappingProfile(id="confirmed", name="confirmed mapping", target=m.get("target", "statement"),
                                  columns=m["columns"], options=m.get("options") or {})
            return prof, prof.columns, prof.options, "confirmed-mapping", []
        store = self.store()
        prof = store.get(self.config["profile_id"]) if self.config.get("profile_id") else store.match(headers)
        if prof is not None:
            return prof, dict(prof.columns), dict(prof.options), f"profile:{prof.id}", []
        if not guess:
            raise ConnectorError(f"{name}: layout not recognised")
        cols, opts, label, w = self.resolve(df, name)
        return None, cols, opts, label, w

    # ------------------------------------------------------------------ Connector API
    def test_connection(self) -> ConnectionTestResult:
        ps = self.paths()
        if not ps:
            return ConnectionTestResult(ok=False, message="not configured: give a file or folder path (config 'path')")
        missing = [str(p) for p in ps if not p.exists()]
        if missing:
            return ConnectionTestResult(ok=False, message=f"file not found: {missing[:3]}")
        try:
            sheet, df = _units(ps[0], self.config.get("sheet"))[0]
            _, _, _, label, _ = self.classify(df, ps[0].name)
        except Exception as e:
            return ConnectionTestResult(ok=False, message=f"{ps[0].name}: {e}")
        return ConnectionTestResult(ok=True, message=f"{len(ps)} file(s) readable; first layout: {label}",
                                    details={"files": len(ps), "layout": label})

    def discover(self) -> list[DatasetInfo]:
        out = []
        for p in self.paths():
            try:
                for sheet, df in _units(p, self.config.get("sheet")):
                    prof, _, opts, label, _ = self.classify(df, p.name)
                    table = {"statement": "external_statement_lines", "journal": "journal_entries", "coa": "accounts",
                             "parties": "counterparties", "invoices": "invoices", "bills": "bills",
                             "cashbook": "journal_entries", "pos_daily": "pos_daily_imports"}.get(prof.target if prof else "statement")
                    out.append(DatasetInfo(id=f"{p.name}{'#' + sheet if sheet else ''}", name=f"{p.name} {sheet or ''} ({label})".strip(),
                                           canonical_table=table, row_estimate=len(df)))
            except Exception:
                continue
        return out

    def pull(self, since: dt.datetime | None = None) -> PullResult:
        ps = self.paths()
        if not ps:
            raise ConnectorError("not configured: give a file or folder path (config 'path')")
        base = (self.config.get("base_currency") or "").upper()
        fx = self.fx()
        catalog = AccountCatalog(self.company_id, self.config.get("accounts"))
        warnings: list[str] = []
        jobs: list[tuple[int, Path, str | None, pd.DataFrame, MappingProfile | None, dict, dict, str]] = []
        for p in ps:
            sheets = _units(p, self.config.get("sheet"))
            for sheet, df in sheets:
                if df.empty:
                    continue
                try:
                    prof, cols, opts, label, w = self.classify(df, p.name, guess=sheet is None or len(sheets) == 1)
                except ConnectorError as e:
                    warnings.append(f"{p.name}[{sheet}]: tab skipped - layout not recognised "
                                    "(propose a mapping for it to include it)")
                    continue
                warnings += w
                target = prof.target if prof else "statement"
                jobs.append((0 if target in LOAD_FIRST else 1, p, sheet, df, prof, cols, opts, label))
        jobs.sort(key=lambda j: j[0])                  # stable: chart of accounts / parties first
        parts: dict[str, list[pd.DataFrame]] = {}
        mapping: dict[str, str] = {}
        parties: dict = {}
        for _, p, sheet, df, prof, cols, opts, label in jobs:
            system = opts.get("source_system") or self.metadata.id
            target = prof.target if prof else "statement"
            if target == "statement":
                channel = opts.get("channel", "bank")
                out, w = build_statement_lines(
                    df, cols, opts, company_id=self.company_id, source_system=system,
                    source_file=p.name, fx=fx, default_currency=self.config.get("currency") or base,
                    account_id=self.account_for(channel),
                    statement_ref=mask_account(self.config.get("statement_ref") or opts.get("statement_ref")),
                    since=since.date() if since else None)
                warnings += w
                if len(out):
                    parts.setdefault("external_statement_lines", []).append(out)
            elif target in READERS:
                if not base:
                    raise ConnectorError("set config['base_currency'] (the company's reporting currency)")
                ctx = LedgerCtx(self.company_id, system, p.name, catalog, fx, base, sheet=sheet, parties=parties)
                frames = READERS[target](df, cols, opts, ctx)
                warnings += ctx.warnings
                for t, f in frames.items():
                    if f is not None and len(f):
                        parts.setdefault(t, []).append(f)
            else:
                raise ConnectorError(f"{p.name}: profile target {target!r} is not supported by this connector")
            mapping = {"_profile": label, **{k: v if isinstance(v, str) else "|".join(map(str, v))
                                             for k, v in cols.items() if not isinstance(v, dict)}}
        frames_out: dict[str, pd.DataFrame] = {}
        for t in TABLE_ORDER:
            if t in parts:
                f = pd.concat(parts[t], ignore_index=True)
                if "id" in f.columns:
                    f = f.drop_duplicates("id", keep="first")
                frames_out[t] = f
        if since is not None:
            warnings.append("'since' filter applied to statement lines only; ledger exports are always read in full")
        return PullResult(frames=frames_out, source_files=[p.name for p in ps], warnings=warnings, column_mapping=mapping)
