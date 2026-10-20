"""Shared machinery of the file connectors (generic CSV/Excel, bank CSV, wallet CSV).

Config keys (all optional unless noted)
  path / paths      file, list of files, or a folder (all *.csv / *.xlsx inside, sorted)   [required]
  profile_id        force one mapping profile (built-in or owner-saved)
  mapping           owner-confirmed mapping: {"columns": {...}, "options": {...}, "target": "statement"}
  base_currency     company base currency (needed to convert foreign-currency lines)
  currency          default currency when the file has no currency column
  fx_rates          {"USD": 7.1} or fx_rates rows / Database for dated rates
  account_id        ledger account the statement belongs to (or account_ids {"bank": ..., "wallet": ...})
  statement_ref     account label (masked to ****1234 before storing)
  sheet             sheet name for .xlsx
  profiles_dir      where owner-confirmed profiles are saved / read
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, ClassVar, Optional

import pandas as pd

from backend.connectors._fx import FxResolver
from backend.connectors._mapping import (MappingProfile, Proposal, ProfileStore, propose_mapping,
                                         propose_mapping_llm, proposal_to_profile)
from backend.connectors._stmt import build_statement_lines
from backend.connectors._util import mask_account, read_table
from backend.connectors.base import (ConnectionTestResult, Connector, ConnectorError, DatasetInfo, PullResult)

FILE_SUFFIXES = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm")


class FileConnector(Connector):
    system_name: ClassVar[str] = ""            # source_system stamped on rows ("" = use the profile's)
    accept: ClassVar[Any] = None               # callable(profile) -> bool: which profiles this connector may use
    default_channel: ClassVar[str] = "bank"

    # ------------------------------------------------------------------ files
    def paths(self) -> list[Path]:
        raw = self.config.get("paths") or self.config.get("path")
        if not raw:
            return []
        items = [raw] if isinstance(raw, (str, Path)) else list(raw)
        out: list[Path] = []
        for it in items:
            p = Path(it)
            if p.is_dir():
                out += sorted(f for f in p.iterdir() if f.suffix.lower() in FILE_SUFFIXES)
            else:
                out.append(p)
        return out

    def store(self) -> ProfileStore:
        return ProfileStore(self.config.get("profiles_dir") or ProfileStore.default_user_dir())

    def fx(self) -> FxResolver:
        base = (self.config.get("base_currency") or self.config.get("currency") or "").upper()
        if not base:
            raise ConnectorError("set config['base_currency'] (the company's reporting currency)")
        src = self.config.get("fx_rates")
        return FxResolver(base, src if src is not None else self.config.get("db"))

    def account_for(self, channel: str) -> Optional[str]:
        return (self.config.get("account_ids") or {}).get(channel) or self.config.get("account_id")

    # ------------------------------------------------------------------ mapping
    def _usable(self, profile: MappingProfile) -> bool:
        return self.accept is None or bool(self.accept(profile))

    def resolve(self, df: pd.DataFrame, name: str) -> tuple[dict[str, Any], dict[str, Any], str, list[str]]:
        """-> (columns, options, mapping_label, warnings) for this file."""
        warnings: list[str] = []
        headers = [c for c in df.columns if not str(c).startswith("_")]
        if self.config.get("mapping"):
            m = self.config["mapping"]
            return m["columns"], dict(m.get("options") or {}), "confirmed-mapping", warnings
        store = self.store()
        if self.config.get("profile_id"):
            prof = store.get(self.config["profile_id"])
        else:
            prof = store.match(headers, "statement")
            if prof is not None and not self._usable(prof):
                raise ConnectorError(f"{name}: layout looks like '{prof.name}', which this connector does not handle; "
                                     "use the generic CSV/Excel connector (csv_upload)")
        if prof is not None:
            return dict(prof.columns), dict(prof.options), f"profile:{prof.id}", warnings
        prop = propose_mapping(df, "statement")
        if "date" not in prop.columns or not ({"amount", "debit", "credit"} & set(prop.columns)):
            raise ConnectorError(f"{name}: could not recognise the layout (date/amount columns missing). "
                                 f"Call propose() and confirm it. Headers: {headers}")
        warnings.append(f"{name}: mapping guessed from header names and NOT yet confirmed by the owner: {prop.columns}")
        opts = dict(prop.options)
        opts.setdefault("channel", self.default_channel)
        return dict(prop.columns), opts, "heuristic", warnings

    def propose(self, path: str | Path, use_llm: bool = False, provider=None, target: str = "statement") -> Proposal:
        """Owner flow, step 1: what does the copilot think the columns are? (nothing is loaded)"""
        df, _ = read_table(path, self.config.get("sheet"))
        headers = [c for c in df.columns if not str(c).startswith("_")]
        known = self.store().match(headers, target)
        if known is not None and self._usable(known):
            return Proposal(target=target, columns={k: v for k, v in known.columns.items() if isinstance(v, str)},
                            options=dict(known.options), source=f"profile:{known.id}",
                            notes=[f"recognised the saved layout '{known.name}'"])
        prop = propose_mapping(df, target)
        prop.options.setdefault("channel", self.default_channel)
        if use_llm:
            if provider is None:
                raise ConnectorError("use_llm=True needs a provider (backend.agent.providers.build_provider(settings))")
            prop = propose_mapping_llm(provider, df, target, prop)
        return prop

    def confirm(self, proposal: Proposal, profile_id: str, name: str, path: str | Path) -> MappingProfile:
        """Owner flow, step 2: the owner accepted/edited the proposal -> save as a reusable profile."""
        df, _ = read_table(path, self.config.get("sheet"))
        prof = proposal_to_profile(proposal, profile_id, name, [c for c in df.columns if not str(c).startswith("_")])
        self.store().save(prof)
        return prof

    # ------------------------------------------------------------------ Connector API
    def test_connection(self) -> ConnectionTestResult:
        ps = self.paths()
        if not ps:
            return ConnectionTestResult(ok=False, message="not configured: give a file or folder path (config 'path')")
        missing = [str(p) for p in ps if not p.exists()]
        if missing:
            return ConnectionTestResult(ok=False, message=f"file not found: {missing[:3]}")
        try:
            df, _ = read_table(ps[0], self.config.get("sheet"))
            _, _, label, _ = self.resolve(df, ps[0].name)
        except Exception as e:
            return ConnectionTestResult(ok=False, message=f"{ps[0].name}: {e}")
        return ConnectionTestResult(ok=True, message=f"{len(ps)} file(s) readable; first layout: {label}",
                                    details={"files": len(ps), "layout": label})

    def discover(self) -> list[DatasetInfo]:
        out = []
        for p in self.paths():
            try:
                df, _ = read_table(p, self.config.get("sheet"))
                out.append(DatasetInfo(id=p.name, name=p.name, canonical_table="external_statement_lines",
                                       row_estimate=len(df)))
            except Exception:
                continue
        return out

    def pull(self, since: dt.datetime | None = None) -> PullResult:
        ps = self.paths()
        if not ps:
            raise ConnectorError("not configured: give a file or folder path (config 'path')")
        fx = self.fx()
        frames, warnings, mapping = [], [], {}
        for p in ps:
            df, _ = read_table(p, self.config.get("sheet"))
            if df.empty:
                warnings.append(f"{p.name}: no data rows")
                continue
            cols, opts, label, w = self.resolve(df, p.name)
            warnings += w
            system = self.system_name or opts.get("source_system") or self.metadata.id
            out, w2 = build_statement_lines(
                df, cols, opts, company_id=self.company_id, source_system=system, source_file=p.name, fx=fx,
                default_currency=self.config.get("currency") or self.config.get("base_currency"), account_id=self.account_for(opts.get("channel", "bank")),
                statement_ref=mask_account(self.config.get("statement_ref") or opts.get("statement_ref")),
                since=since.date() if since else None)
            warnings += w2
            if len(out):
                frames.append(out)
            mapping = {"_profile": label, **{k: v if isinstance(v, str) else "|".join(v) for k, v in cols.items()}}
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return PullResult(frames={"external_statement_lines": frame} if len(frame) else {},
                          source_files=[p.name for p in ps], warnings=warnings, column_mapping=mapping)
