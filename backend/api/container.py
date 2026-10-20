"""Wires every component together once per process (or per test)."""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path

from backend import instance
from backend.agent.executor import ToolExecutor
from backend.agent.loop import Agent, SessionStore
from backend.agent.providers import CostGuard, GuardedProvider, LLMProvider, PricingTable, build_provider
from backend.agent.tools import ToolRegistry, default_registry
from backend.config import Settings
from backend.connectors.base import ConnectorRegistry, registry as global_connector_registry
from backend.core.ids import reseed
from backend.ledger.db import DATA_TABLES, SYSTEM_TABLES, Database, connect
from backend.ledger.demo_companies import seed_demo_companies
from backend.ledger.dev_seed import DEV_COMPANY_ID, seed_dev_fixture
from backend.security.approvals import ApprovalQueue
from backend.security.audit import AuditLog
from backend.security.permissions import PermissionStore
from backend.security.redaction import Redactor
from backend.security.tiers import Tier


@dataclass
class Container:
    settings: Settings
    db: Database
    audit: AuditLog
    approvals: ApprovalQueue
    permissions: PermissionStore
    tools: ToolRegistry
    executor: ToolExecutor
    cost_guard: CostGuard
    provider: GuardedProvider
    agent: Agent
    connectors: ConnectorRegistry
    redactor: Redactor


def build_container(settings: Settings, *, db: Database | None = None, provider: LLMProvider | None = None,
                    connectors: ConnectorRegistry | None = None, engine_factory=None) -> Container:
    db = db or connect(settings.resolved_duckdb_path())
    audit = AuditLog(db)
    approvals = ApprovalQueue(db, audit)
    permissions = PermissionStore(db, audit, settings.default_company_tier)
    warning = instance.private_dir_warning(settings)
    if warning:
        audit.append("private_dir_not_ignored", actor="system", payload={"warning": warning})
    if connectors is None:
        connectors = global_connector_registry
        try:
            connectors.autodiscover()
        except Exception as e:  # a broken connector module must not take the API down
            audit.append("connector_discovery_error", actor="system", payload={"error": f"{type(e).__name__}: {e}"})
        if settings.connector_plugin_dirs:  # private / extra connectors from outside the repo
            loaded, errors = instance.load_connector_plugins(settings.connector_plugin_dirs)
            audit.append("connector_plugins_loaded", actor="system", payload={"loaded": loaded, "errors": errors})
    tools = default_registry()
    executor = ToolExecutor(db, tools, permissions, approvals, audit, t3_daily_cap=settings.t3_daily_cap,
                            settings=settings, connector_registry=connectors, engine_factory=engine_factory)
    guard = CostGuard(db, audit, PricingTable.load(settings.llm_pricing_file or None),
                      daily_cap_usd=settings.llm_daily_usd_cap, request_cap_usd=settings.llm_request_usd_cap)
    guarded = GuardedProvider(provider or build_provider(settings), guard)
    redactor = Redactor(settings.redact_names)
    agent = Agent(db, guarded, tools, executor, permissions, audit, redactor, sessions=SessionStore(),
                  max_steps=settings.agent_max_steps, max_tokens=settings.llm_max_output_tokens)

    ct = Container(settings, db, audit, approvals, permissions, tools, executor, guard, guarded, agent,
                   connectors, redactor)
    seed_state(ct)
    return ct


def seed_state(ct: Container, *, run_data_hook: bool = False) -> dict:
    """Built-in fixtures (+ optionally D1's data hook). Idempotent; returns what was loaded."""
    report: dict = {}
    if ct.settings.seed_dev_fixture:
        loaded = seed_dev_fixture(ct.db)
        if loaded:
            ct.audit.append("dev_fixture_seeded", actor="system", company_id=DEV_COMPANY_ID,
                            payload={t: {"inserted": r["inserted"], "injection_flags": r["injection_flags"]}
                                     for t, r in loaded.items()})
            # The dev fixture demonstrates the approval flow, so it gets T2.
            ct.permissions.set_tier(DEV_COMPANY_ID, Tier.T2, actor="system:dev_seed")
            report["dev_fixture"] = {t: r["inserted"] for t, r in loaded.items()}
    if ct.settings.seed_demo_companies:
        report["demo_companies"] = seed_demo_companies(ct.db)["inserted"]
    if ct.settings.companies_file:  # this instance's own companies + initial tiers
        rows = instance.read_companies_file(ct.settings.companies_file)
        loaded = instance.load_companies(ct.db, rows, Path(ct.settings.companies_file).name)
        tiers = instance.apply_config_tiers(rows, ct.permissions, ct.db)
        if loaded["inserted"] or tiers:
            ct.audit.append("instance_companies_loaded", actor="system",
                            payload={"inserted": loaded["inserted"], "tiers": tiers})
        report["instance_companies"] = {"inserted": loaded["inserted"], "tiers": tiers}
    if run_data_hook and ct.settings.demo_seed_module:
        report["data_hook"] = _run_data_hook(ct)
    return report


def _run_data_hook(ct: Container) -> dict:
    """D1's optional loader: module `DEMO_SEED_MODULE` with `seed(db) -> dict`."""
    name = ct.settings.demo_seed_module
    try:
        mod = importlib.import_module(name)
    except ModuleNotFoundError as e:
        if e.name and name.startswith(e.name):
            return {"module": name, "status": "not_found"}
        raise
    result = mod.seed(ct.db) or {}
    ct.audit.append("demo_data_seeded", actor="system", payload={"module": name, "result": result})
    return {"module": name, "status": "ok", "result": result}


def reset_state(ct: Container, *, seed: int | str | None = None) -> dict:
    """Wipe every data and system table, clear chat sessions, reload the seeded state.

    The audit chain restarts; its first row records the previous chain head, so the reset
    itself stays traceable. Only reachable via POST /admin/reset when ADMIN_RESET_ENABLED=1.
    """
    old_head = ct.db.query_one("SELECT seq, row_hash FROM audit_head WHERE id = 1")
    with ct.db.locked() as conn:
        for table in DATA_TABLES + SYSTEM_TABLES:
            conn.execute(f"DELETE FROM {table}")
    ct.agent.sessions.clear()
    inner = ct.provider.inner
    if hasattr(inner, "calls"):
        inner.calls.clear()
    reseed(seed)
    ct.audit.append("admin_reset", actor="owner", payload={
        "previous_head": old_head, "seed": seed})
    report = seed_state(ct, run_data_hook=True)
    return {"ok": True, "seed": seed, "previous_head": old_head, "loaded": report,
            "companies": [c["id"] for c in ct.db.query_dicts("SELECT id FROM companies ORDER BY id")]}
