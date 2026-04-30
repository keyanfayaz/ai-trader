from __future__ import annotations

from functools import lru_cache

from app.clients.alpaca_client import AlpacaClient
from app.clients.openai_client import build_openai_client
from app.logging_config import configure_logging
from app.repositories.json_agent_store import JsonAgentStore
from app.repositories.markdown_research_store import MarkdownResearchStore
from app.repositories.runtime_config_store import RuntimeConfigStore
from app.services.agent_management_service import AgentManagementService
from app.services.agent_monitor_stream import AgentMonitorStream
from app.services.agent_scheduler import AgentScheduler
from app.services.market_research_service import MarketResearchService
from app.services.trading_service import TradingService
from app.settings import Settings, load_settings


@lru_cache
def get_settings() -> Settings:
    settings = load_settings()
    configure_logging(settings.log_level)
    return settings


@lru_cache
def get_runtime_config_store() -> RuntimeConfigStore:
    settings = get_settings()
    return RuntimeConfigStore(
        file_path=settings.runtime_config_path,
        default_trading_mode=settings.trading_mode,
    )


def get_trading_mode() -> str:
    settings = get_settings()
    runtime_store = get_runtime_config_store()
    mode = runtime_store.get_trading_mode()
    return mode if mode in {"paper", "real"} else settings.trading_mode


def _build_alpaca_client_for_mode(mode: str) -> AlpacaClient:
    settings = get_settings()
    if mode == "real":
        return AlpacaClient(
            api_key=settings.alpaca_real_api_key,
            secret_key=settings.alpaca_real_secret_key,
            trading_base_url=settings.alpaca_real_base_url,
            paper=False,
        )
    return AlpacaClient(
        api_key=settings.alpaca_paper_api_key,
        secret_key=settings.alpaca_paper_secret_key,
        trading_base_url=settings.alpaca_paper_base_url,
        paper=True,
    )


def get_alpaca_client() -> AlpacaClient:
    return _build_alpaca_client_for_mode(get_trading_mode())


@lru_cache
def get_openai_client():
    settings = get_settings()
    return build_openai_client(settings.openai_api_key)


def get_trading_service() -> TradingService:
    return TradingService(
        settings=get_settings(),
        alpaca_client_provider=get_alpaca_client,
        openai_client=get_openai_client(),
    )


def get_market_research_service() -> MarketResearchService:
    return MarketResearchService(
        settings=get_settings(),
        alpaca_client=get_alpaca_client(),
        research_store=get_research_store(),
        openai_client=get_openai_client(),
    )


@lru_cache
def get_agent_store() -> JsonAgentStore:
    settings = get_settings()
    return JsonAgentStore(file_path=settings.agent_store_path)


@lru_cache
def get_research_store() -> MarkdownResearchStore:
    settings = get_settings()
    return MarkdownResearchStore(
        base_dir=settings.research_store_dir,
        retention_days=settings.research_retention_days,
    )


@lru_cache
def get_agent_monitor_stream() -> AgentMonitorStream:
    return AgentMonitorStream()


def get_agent_management_service() -> AgentManagementService:
    return AgentManagementService(
        settings=get_settings(),
        store=get_agent_store(),
        market_research_service=get_market_research_service(),
        trading_service=get_trading_service(),
        monitor_stream=get_agent_monitor_stream(),
    )


@lru_cache
def get_agent_scheduler() -> AgentScheduler:
    settings = get_settings()
    return AgentScheduler(
        agent_service=get_agent_management_service(),
        interval_seconds=settings.agent_run_interval_seconds,
    )
