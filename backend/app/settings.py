from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
ROOT_DIR = BACKEND_DIR.parent
DEFAULT_ENV_PATH = BACKEND_DIR / ".env"


@dataclass(slots=True)
class Settings:
    alpaca_paper_api_key: str
    alpaca_paper_secret_key: str
    alpaca_paper_base_url: str
    alpaca_real_api_key: str
    alpaca_real_secret_key: str
    alpaca_real_base_url: str
    trading_mode: str
    databento_api_key: str
    massive_api_key: str
    topstepx_api_key: str
    topstepx_username: str
    data_provider: str
    broker_provider: str
    openai_api_key: str
    openai_model: str
    default_symbol: str
    bar_timeframe: str
    fast_sma_window: int
    slow_sma_window: int
    order_qty: float
    log_level: str
    agent_store_path: str = str(ROOT_DIR / "data" / "agents.json")
    runtime_config_path: str = str(ROOT_DIR / "data" / "runtime_config.json")
    research_store_dir: str = str(ROOT_DIR / "data" / "research")
    skills_store_dir: str = str(ROOT_DIR / "data" / "skills")
    research_retention_days: int = 14
    research_candidate_scan_limit: int = 80
    research_quality_universe_limit: int = 10
    research_min_price: float = 20.0
    research_min_dollar_volume: float = 50_000_000.0
    research_min_average_volume: float = 1_000_000.0
    research_min_volume_ratio: float = 0.8
    research_max_volume_ratio: float = 3.0
    agent_run_interval_seconds: int = 60
    summary_starting_deposit: float = 100.0
    mission_window_min_seconds: int = 300
    mission_window_max_seconds: int = 14_400
    mission_window_default_seconds: int = 1_800
    mission_tick_seconds: int = 45
    mission_checkpoint_move_pct: float = 1.5


def load_settings(env_path: Path | None = None) -> Settings:
    """Load runtime settings from .env and environment variables."""
    chosen_env_path = env_path or DEFAULT_ENV_PATH
    if chosen_env_path.exists():
        load_dotenv(chosen_env_path)

    settings = Settings(
        alpaca_paper_api_key=_get_required("ALPACA_PAPER_API_KEY"),
        alpaca_paper_secret_key=_get_required("ALPACA_PAPER_SECRET_KEY"),
        alpaca_paper_base_url=os.getenv("ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/"),
        alpaca_real_api_key=_get_required("ALPACA_REAL_API_KEY"),
        alpaca_real_secret_key=_get_required("ALPACA_REAL_SECRET_KEY"),
        alpaca_real_base_url=os.getenv("ALPACA_REAL_BASE_URL", "https://api.alpaca.markets").rstrip("/"),
        trading_mode=os.getenv("TRADING_MODE", "paper").strip().lower(),
        databento_api_key=os.getenv("DATABENTO_API_KEY", "").strip(),
        massive_api_key=os.getenv("MASSIVE_API_KEY", "").strip(),
        topstepx_api_key=os.getenv("TOPSTEPX_API_KEY", "").strip(),
        topstepx_username=os.getenv("TOPSTEPX_USERNAME", "").strip(),
        data_provider=os.getenv("DATA_PROVIDER", "alpaca").strip().lower(),
        broker_provider=os.getenv("BROKER_PROVIDER", "alpaca").strip().lower(),
        openai_api_key=_get_required("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.4"),
        default_symbol=os.getenv("DEFAULT_SYMBOL", "AAPL").upper(),
        bar_timeframe=os.getenv("BAR_TIMEFRAME", "1Day"),
        fast_sma_window=_get_int("FAST_SMA_WINDOW", default=5, minimum=1),
        slow_sma_window=_get_int("SLOW_SMA_WINDOW", default=20, minimum=2),
        order_qty=_get_float("ORDER_QTY", default=1.0, minimum=0.0000001),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        agent_store_path=os.getenv("AGENT_STORE_PATH", str(ROOT_DIR / "data" / "agents.json")).strip(),
        runtime_config_path=os.getenv("RUNTIME_CONFIG_PATH", str(ROOT_DIR / "data" / "runtime_config.json")).strip(),
        research_store_dir=os.getenv("RESEARCH_STORE_DIR", str(ROOT_DIR / "data" / "research")).strip(),
        skills_store_dir=os.getenv("SKILLS_STORE_DIR", str(ROOT_DIR / "data" / "skills")).strip(),
        research_retention_days=_get_int("RESEARCH_RETENTION_DAYS", default=14, minimum=1),
        research_candidate_scan_limit=_get_int("RESEARCH_CANDIDATE_SCAN_LIMIT", default=80, minimum=10),
        research_quality_universe_limit=_get_int("RESEARCH_QUALITY_UNIVERSE_LIMIT", default=10, minimum=1),
        research_min_price=_get_float("RESEARCH_MIN_PRICE", default=20.0, minimum=0.01),
        research_min_dollar_volume=_get_float(
            "RESEARCH_MIN_DOLLAR_VOLUME",
            default=50_000_000.0,
            minimum=1.0,
        ),
        research_min_average_volume=_get_float(
            "RESEARCH_MIN_AVERAGE_VOLUME",
            default=1_000_000.0,
            minimum=1.0,
        ),
        research_min_volume_ratio=_get_float("RESEARCH_MIN_VOLUME_RATIO", default=0.8, minimum=0.01),
        research_max_volume_ratio=_get_float("RESEARCH_MAX_VOLUME_RATIO", default=3.0, minimum=0.01),
        agent_run_interval_seconds=_get_int("AGENT_RUN_INTERVAL_SECONDS", default=60, minimum=5),
        summary_starting_deposit=_get_float("SUMMARY_STARTING_DEPOSIT", default=100.0, minimum=0.0),
        mission_window_min_seconds=_get_int("MISSION_WINDOW_MIN_SECONDS", default=300, minimum=60),
        mission_window_max_seconds=_get_int("MISSION_WINDOW_MAX_SECONDS", default=14_400, minimum=60),
        mission_window_default_seconds=_get_int("MISSION_WINDOW_DEFAULT_SECONDS", default=1_800, minimum=60),
        mission_tick_seconds=_get_int("MISSION_TICK_SECONDS", default=45, minimum=5),
        mission_checkpoint_move_pct=_get_float("MISSION_CHECKPOINT_MOVE_PCT", default=1.5, minimum=0.1),
    )
    _validate_settings(settings)
    return settings


def _get_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _get_int(name: str, default: int, minimum: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return value


def _get_float(name: str, default: float, minimum: float) -> float:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return value


def _validate_settings(settings: Settings) -> None:
    if settings.fast_sma_window >= settings.slow_sma_window:
        raise ValueError("FAST_SMA_WINDOW must be smaller than SLOW_SMA_WINDOW.")
    if settings.research_quality_universe_limit > settings.research_candidate_scan_limit:
        raise ValueError("RESEARCH_QUALITY_UNIVERSE_LIMIT must be <= RESEARCH_CANDIDATE_SCAN_LIMIT.")
    if settings.research_min_volume_ratio > settings.research_max_volume_ratio:
        raise ValueError("RESEARCH_MIN_VOLUME_RATIO must be <= RESEARCH_MAX_VOLUME_RATIO.")
    if settings.mission_window_min_seconds > settings.mission_window_max_seconds:
        raise ValueError("MISSION_WINDOW_MIN_SECONDS must be <= MISSION_WINDOW_MAX_SECONDS.")
    if not (
        settings.mission_window_min_seconds
        <= settings.mission_window_default_seconds
        <= settings.mission_window_max_seconds
    ):
        raise ValueError("MISSION_WINDOW_DEFAULT_SECONDS must be within the min/max window bounds.")
    if settings.trading_mode not in {"paper", "real"}:
        raise ValueError("TRADING_MODE must be either 'paper' or 'real'.")
    from app.clients.providers.base import PROVIDER_REGISTRY

    data_caps = PROVIDER_REGISTRY.get(settings.data_provider)
    if data_caps is None or not data_caps.market_data:
        raise ValueError(
            f"DATA_PROVIDER must be a market-data-capable provider; got '{settings.data_provider}'."
        )
    broker_caps = PROVIDER_REGISTRY.get(settings.broker_provider)
    if broker_caps is None or not broker_caps.trading:
        raise ValueError(
            f"BROKER_PROVIDER must be a trade-capable provider; got '{settings.broker_provider}'."
        )
    if "paper-api.alpaca.markets" not in settings.alpaca_paper_base_url:
        raise ValueError("ALPACA_PAPER_BASE_URL must use the Alpaca paper endpoint.")
    if "api.alpaca.markets" not in settings.alpaca_real_base_url:
        raise ValueError("ALPACA_REAL_BASE_URL must use the Alpaca live endpoint.")


def _get_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name, "true" if default else "false").strip().lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean (true/false).")
