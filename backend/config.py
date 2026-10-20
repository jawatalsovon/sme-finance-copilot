"""Runtime settings, read from environment variables and .env files.

Load order (first value wins): real environment > $COPILOT_PRIVATE_DIR/.env > backend/.env.
Everything instance-specific - companies, tiers, connectors, LLM settings, DB path - comes
from here, so a private self-hosted instance needs no code changes (see backend/README.md,
"Running a private instance").

Secrets (API keys, client secrets, owner token) are held in the Settings object but are
excluded from repr() and from `public_dict()`, so they never end up in logs or API responses.
Set COPILOT_SKIP_DOTENV=1 to ignore .env files (the test suite always does).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent

_SECRET_FIELDS = {"openai_api_key", "anthropic_api_key", "qbo_client_secret", "owner_api_token"}
APP_ENVS = ("dev", "demo", "prod")


def _under_pytest() -> bool:
    return "pytest" in sys.modules and os.environ.get("COPILOT_ALLOW_REAL_LLM") != "1"


def _load_dotenv_once() -> None:
    # Tests never read .env files, so no real key can be picked up by accident.
    if os.environ.get("COPILOT_SKIP_DOTENV") == "1" or _under_pytest():
        return
    from dotenv import load_dotenv

    def load_private() -> None:
        priv = os.environ.get("COPILOT_PRIVATE_DIR", "").strip()
        if priv and (Path(priv) / ".env").exists():
            load_dotenv(Path(priv) / ".env", override=False)

    load_private()                       # private instance config first ...
    if (BACKEND_DIR / ".env").exists():  # ... then the repo's backend/.env (never overrides)
        load_dotenv(BACKEND_DIR / ".env", override=False)
    load_private()                       # in case backend/.env is what set COPILOT_PRIVATE_DIR


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    return default if not raw else raw in ("1", "true", "yes", "on")


@dataclass
class Settings:
    app_env: str = "dev"                       # dev | demo | prod; outside dev the owner token is mandatory
    llm_provider: str = "mock"
    llm_model: str = ""
    openai_api_key: str = field(default="", repr=False)
    anthropic_api_key: str = field(default="", repr=False)
    llm_daily_usd_cap: float = 1.0
    llm_request_usd_cap: float = 0.10
    llm_max_output_tokens: int = 1200
    llm_pricing_file: str = ""
    private_dir: str = ""                      # COPILOT_PRIVATE_DIR: instance config/data outside the repo
    duckdb_path: str = "data/copilot.duckdb"   # relative -> private_dir if set, else repo root
    companies_file: str = ""                   # JSON list of companies (+ granted_tier) for this instance
    connector_plugin_dirs: list[str] = field(default_factory=list)  # extra connector modules (*.py)
    seed_dev_fixture: bool = False             # tiny fictional 'devco' company - tests only
    seed_demo_companies: bool = True           # the three demo SMEs (bd_grocer, cn_xborder, uk_garden)
    demo_seed_module: str = "scripts.data.seed"  # optional D1 hook: module with seed(db) -> dict
    default_company_tier: str = "T1"
    t3_daily_cap: int = 20
    owner_api_token: str = field(default="", repr=False)
    redact_names: list[str] = field(default_factory=list)
    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    qbo_client_id: str = ""
    qbo_client_secret: str = field(default="", repr=False)
    qbo_env: str = "sandbox"
    qbo_redirect_uri: str = "http://localhost:8000/connectors/qbo/callback"
    agent_max_steps: int = 6
    admin_reset_enabled: bool = False          # POST /admin/reset (local harness only)

    @property
    def owner_auth_required(self) -> bool:
        """Outside dev, owner actions fail closed: no configured token -> nobody can do them."""
        return self.app_env != "dev"

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv_once()
        csv = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
        provider = _env("LLM_PROVIDER", "mock").lower() or "mock"
        if _under_pytest():
            provider = "mock"  # CLAUDE.md: tests always use the $0 mock provider
        app_env = _env("APP_ENV", "dev").lower() or "dev"
        if _env_bool("DEMO_MODE", False) and app_env == "dev":
            app_env = "demo"
        if app_env not in APP_ENVS:
            raise ValueError(f"APP_ENV must be one of {APP_ENVS}, got {app_env!r}")
        private_dir = _env("COPILOT_PRIVATE_DIR")
        companies_file = _env("COMPANIES_FILE")
        if not companies_file and private_dir and (Path(private_dir) / "companies.json").exists():
            companies_file = str(Path(private_dir) / "companies.json")
        plugin_dirs = [p for p in _env("CONNECTOR_PLUGIN_DIRS").replace(os.pathsep, ",").split(",") if p.strip()]
        if not plugin_dirs and private_dir and (Path(private_dir) / "connectors").is_dir():
            plugin_dirs = [str(Path(private_dir) / "connectors")]
        return cls(
            app_env=app_env,
            llm_provider=provider,
            llm_model=_env("LLM_MODEL"),
            openai_api_key=_env("OPENAI_API_KEY"),
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            llm_daily_usd_cap=_env_float("LLM_DAILY_USD_CAP", 1.0),
            llm_request_usd_cap=_env_float("LLM_REQUEST_USD_CAP", 0.10),
            llm_max_output_tokens=_env_int("LLM_MAX_OUTPUT_TOKENS", 1200),
            llm_pricing_file=_env("LLM_PRICING_FILE"),
            private_dir=private_dir,
            duckdb_path=_env("DUCKDB_PATH", "copilot.duckdb" if private_dir else "data/copilot.duckdb"),
            companies_file=companies_file,
            connector_plugin_dirs=[p.strip() for p in plugin_dirs],
            seed_dev_fixture=_env_bool("SEED_DEV_FIXTURE", False),
            seed_demo_companies=_env_bool("SEED_DEMO_COMPANIES", True),
            demo_seed_module=_env("DEMO_SEED_MODULE", "scripts.data.seed"),
            admin_reset_enabled=_env_bool("ADMIN_RESET_ENABLED", False),
            default_company_tier=_env("DEFAULT_COMPANY_TIER", "T1").upper(),
            t3_daily_cap=_env_int("T3_DAILY_CAP", 20),
            owner_api_token=_env("OWNER_API_TOKEN"),
            redact_names=csv(_env("REDACT_NAMES")),
            cors_origins=csv(_env("CORS_ORIGINS"))
            or ["http://localhost:3000", "http://127.0.0.1:3000"],
            qbo_client_id=_env("QBO_CLIENT_ID"),
            qbo_client_secret=_env("QBO_CLIENT_SECRET"),
            qbo_env=_env("QBO_ENV", "sandbox").lower(),
            qbo_redirect_uri=_env(
                "QBO_REDIRECT_URI", "http://localhost:8000/connectors/qbo/callback"
            ),
        )

    def instance_path(self, value: str) -> Path:
        """Relative instance paths resolve against private_dir if set, else the repo root."""
        p = Path(value)
        return p if p.is_absolute() else (Path(self.private_dir) if self.private_dir else REPO_ROOT) / p

    def resolved_duckdb_path(self) -> str:
        """Per-instance database file; ':memory:' is kept as is."""
        if self.duckdb_path == ":memory:":
            return self.duckdb_path
        p = self.instance_path(self.duckdb_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)

    def public_dict(self) -> dict:
        """Settings safe to show in /health or logs (secrets reduced to set/unset)."""
        out = {}
        for f in fields(self):
            val = getattr(self, f.name)
            out[f.name] = ("set" if val else "unset") if f.name in _SECRET_FIELDS else val
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
