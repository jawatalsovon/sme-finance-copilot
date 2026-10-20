"""Instance configuration: one code base, many deployments (public demo, private self-hosted).

Everything that differs between instances lives outside the code:
  * companies + their initial permission tier  -> a JSON file (COMPANIES_FILE, or
    $COPILOT_PRIVATE_DIR/companies.json)
  * extra / private connectors                -> *.py modules in CONNECTOR_PLUGIN_DIRS
    (or $COPILOT_PRIVATE_DIR/connectors), registered with backend.connectors.register
  * LLM provider, model, keys, caps, tiers     -> environment / $COPILOT_PRIVATE_DIR/.env
  * database                                   -> DUCKDB_PATH (relative to the private dir)

companies.json format (list):
  [{"id": "my_co", "name": "My Company", "country": "GB", "base_currency": "GBP",
    "industry": "retail", "fiscal_year_start_month": 4, "granted_tier": "T2"}]
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from backend.config import REPO_ROOT, Settings
from backend.core.ids import is_valid_id
from backend.ledger.db import Database
from backend.ledger.loader import load_dataframe
from backend.security.tiers import Tier

COMPANY_FIELDS = {"id", "name", "country", "base_currency", "industry", "fiscal_year_start_month",
                  "founded_on", "description", "granted_tier"}
REQUIRED_COMPANY_FIELDS = ("id", "name", "country", "base_currency")


class InstanceConfigError(ValueError):
    pass


def read_companies_file(path: str | Path) -> list[dict]:
    p = Path(path)
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise InstanceConfigError(f"cannot read companies file {p.name}: {type(e).__name__}") from e
    if not isinstance(rows, list):
        raise InstanceConfigError(f"{p.name}: expected a JSON list of companies")
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise InstanceConfigError(f"{p.name}[{i}]: expected an object")
        unknown = set(r) - COMPANY_FIELDS
        missing = [f for f in REQUIRED_COMPANY_FIELDS if not r.get(f)]
        if unknown or missing:
            raise InstanceConfigError(f"{p.name}[{i}]: unknown fields {sorted(unknown)}, missing {missing}")
        if not is_valid_id(r["id"]):
            raise InstanceConfigError(f"{p.name}[{i}]: invalid id {r['id']!r}")
        if r.get("granted_tier"):
            Tier.parse(r["granted_tier"])
    return rows


def load_companies(db: Database, rows: list[dict], source_file: str, source_system: str = "instance_config") -> dict:
    """Insert company rows that do not exist yet (existing ones are left untouched)."""
    if not rows:
        return {"inserted": 0}
    df = pd.DataFrame([{k: v for k, v in r.items() if k != "granted_tier"} for r in rows])
    df["source_system"] = source_system
    df["source_file"] = source_file
    df["source_row_id"] = df["id"]
    return load_dataframe(db, "companies", df, if_exists="skip").as_dict()


def apply_config_tiers(rows: list[dict], permissions, db: Database) -> dict[str, str]:
    """The config tier is the INITIAL grant: applied only where the owner has not set one yet,
    so a later owner decision (PUT /companies/{id}/permissions) survives restarts."""
    applied = {}
    for r in rows:
        if r.get("granted_tier") and not db.scalar(
                "SELECT 1 FROM company_permissions WHERE company_id = ?", [r["id"]]):
            permissions.set_tier(r["id"], r["granted_tier"], actor="system:instance_config")
            applied[r["id"]] = Tier.parse(r["granted_tier"]).value
    return applied


_loaded_plugins: dict[str, str] = {}


def load_connector_plugins(dirs: list[str]) -> tuple[list[str], list[dict]]:
    """Import every *.py in the plugin dirs (not starting with '_'). Each module registers its
    connectors with backend.connectors.register. Returns (loaded files, errors). A broken
    plugin is reported, never fatal. Each file is imported at most once per process."""
    loaded, errors = [], []
    for d in dirs:
        base = Path(d)
        if not base.is_dir():
            errors.append({"path": str(base.name), "error": "plugin directory not found"})
            continue
        for f in sorted(base.glob("*.py")):
            if f.name.startswith("_"):
                continue
            key = str(f.resolve())
            if key in _loaded_plugins:
                loaded.append(f.name)
                continue
            mod_name = f"copilot_plugins.{f.stem}_{hashlib.sha256(key.encode()).hexdigest()[:8]}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, f)
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)
            except Exception as e:
                sys.modules.pop(mod_name, None)
                errors.append({"path": f.name, "error": f"{type(e).__name__}: {e}"})
                continue
            _loaded_plugins[key] = mod_name
            loaded.append(f.name)
    return loaded, errors


def private_dir_warning(settings: Settings) -> str | None:
    """Warn if the private dir sits inside the repo without being git-ignored."""
    if not settings.private_dir:
        return None
    p = Path(settings.private_dir).resolve()
    try:
        rel = p.relative_to(REPO_ROOT)
    except ValueError:
        return None  # outside the repo: fine
    if not rel.parts:
        return "COPILOT_PRIVATE_DIR must not be the repository root"
    try:
        # trailing slash: directory patterns like "data/private/" match even before the dir exists
        ignored = subprocess.run(["git", "check-ignore", "-q", rel.as_posix() + "/"], cwd=REPO_ROOT,
                                 capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ignored = False
    return None if ignored else ("COPILOT_PRIVATE_DIR is inside the repository and NOT git-ignored; "
                                 "move it outside the repo or add it to .gitignore")


def describe(settings: Settings) -> dict[str, Any]:
    """Non-secret summary of where this instance gets its configuration."""
    return {
        "app_env": settings.app_env,
        "private_dir": "set" if settings.private_dir else "unset",
        "companies_file": Path(settings.companies_file).name if settings.companies_file else None,
        "connector_plugin_dirs": len(settings.connector_plugin_dirs),
        "duckdb": "memory" if settings.duckdb_path == ":memory:" else Path(settings.duckdb_path).name,
    }
