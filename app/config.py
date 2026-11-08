from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    data_mode: str
    network_enabled: bool
    crypto_provider: str
    equity_provider: str
    equity_eod_provider: str
    alpaca_feed: str
    database_path: Path
    app_log_path: Path
    audit_log_path: Path
    contracts_dir: Path
    snapshot_dir: Path
    alpaca_key_id: str | None
    alpaca_secret: str | None
    twelve_data_key: str | None
    alpha_vantage_key: str | None
    tiingo_token: str | None
    massive_key: str | None
    coingecko_demo_key: str | None
    fred_api_key: str | None
    sec_user_agent: str
    codex_command: str
    codex_default_model: str
    codex_timeout_seconds: int
    market_cache_ttl_seconds: int

    @classmethod
    def load(cls) -> Settings:
        root = Path(os.environ.get("AMW_ROOT", r"E:\AI-Market-Workbench")).resolve()
        if root.drive.upper() != "E:":
            raise RuntimeError("AMW_ROOT must be located on the E: drive")
        mode = os.environ.get("AMW_DATA_MODE", "fixture").strip().lower()
        if mode not in {"fixture", "live"}:
            raise RuntimeError("AMW_DATA_MODE must be fixture or live")
        network_enabled = os.environ.get("AMW_NETWORK_ENABLED", "false").strip().lower() == "true"
        if mode == "live" and not network_enabled:
            raise RuntimeError("live mode requires AMW_NETWORK_ENABLED=true")
        crypto_provider = os.environ.get("AMW_CRYPTO_PROVIDER", "okx").strip().lower()
        if crypto_provider not in {"binance", "okx"}:
            raise RuntimeError("AMW_CRYPTO_PROVIDER must be binance or okx")
        equity_provider = os.environ.get("AMW_EQUITY_PROVIDER", "alpaca").strip().lower()
        if equity_provider not in {"alpaca", "twelve_data", "alpha_vantage", "tiingo", "massive"}:
            raise RuntimeError("AMW_EQUITY_PROVIDER must be alpaca, twelve_data, alpha_vantage, tiingo or massive")
        # Daily/weekly/monthly can use a different (longer-history) source than intraday.
        equity_eod_provider = os.environ.get("AMW_EQUITY_EOD_PROVIDER", "").strip().lower() or equity_provider
        if equity_eod_provider not in {"alpaca", "twelve_data", "alpha_vantage", "tiingo", "massive"}:
            raise RuntimeError("AMW_EQUITY_EOD_PROVIDER must be alpaca, twelve_data, alpha_vantage, tiingo or massive")
        alpaca_feed = os.environ.get("AMW_ALPACA_FEED", "sip").strip().lower()
        if alpaca_feed not in {"iex", "sip"}:
            raise RuntimeError("AMW_ALPACA_FEED must be iex or sip")
        return cls(
            root=root,
            data_mode=mode,
            network_enabled=network_enabled,
            crypto_provider=crypto_provider,
            equity_provider=equity_provider,
            equity_eod_provider=equity_eod_provider,
            alpaca_feed=alpaca_feed,
            database_path=root / "data" / "workbench.db",
            app_log_path=root / "logs" / "app" / "app.jsonl",
            audit_log_path=root / "logs" / "audit" / "audit.jsonl",
            contracts_dir=root / "contracts",
            snapshot_dir=root / "data" / "snapshots",
            alpaca_key_id=os.environ.get("ALPACA_MARKET_DATA_KEY_ID") or None,
            alpaca_secret=os.environ.get("ALPACA_MARKET_DATA_SECRET") or None,
            twelve_data_key=os.environ.get("TWELVE_DATA_API_KEY") or None,
            alpha_vantage_key=os.environ.get("ALPHA_VANTAGE_API_KEY") or None,
            tiingo_token=os.environ.get("TIINGO_API_TOKEN") or None,
            massive_key=os.environ.get("MASSIVE_API_KEY") or None,
            coingecko_demo_key=os.environ.get("COINGECKO_DEMO_API_KEY") or None,
            fred_api_key=os.environ.get("FRED_API_KEY") or None,
            sec_user_agent=os.environ.get(
                "SEC_USER_AGENT", "AI-Market-Workbench admin@example.invalid"
            ),
            codex_command=os.environ.get("AMW_CODEX_COMMAND", "codex"),
            codex_default_model=os.environ.get("AMW_CODEX_MODEL", "gpt-5.6-terra"),
            codex_timeout_seconds=int(os.environ.get("AMW_CODEX_TIMEOUT_SECONDS", "300")),
            market_cache_ttl_seconds=int(
                os.environ.get("AMW_MARKET_CACHE_TTL_SECONDS", "300")
            ),
        )

    def ensure_runtime_directories(self) -> None:
        for path in (
            self.database_path.parent,
            self.app_log_path.parent,
            self.audit_log_path.parent,
            self.contracts_dir,
            self.snapshot_dir,
            self.root / "cache" / "http",
            self.root / "tmp",
            self.root / "data" / "model-runs",
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings.load()
