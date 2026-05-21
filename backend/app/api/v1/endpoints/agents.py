from __future__ import annotations

import asyncio
import json
from queue import Empty, Queue
from threading import Thread
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import (
    get_agent_management_service,
    get_agent_monitor_stream,
    get_provider_selection,
)
from app.clients.providers.base import PROVIDER_REGISTRY
from app.api.schemas import (
    AgentCreateRequest,
    AgentDeleteResponse,
    AgentGenerateResponse,
    AgentListResponse,
    AgentMonitorResponse,
    AgentResponse,
    AgentRunDetail,
    AgentRunTrade,
    AgentRunSummary,
    AgentSkillsUpdateRequest,
    MissionApproveRequest,
    MissionCreateRequest,
    MissionGenerateRequest,
    MissionGenerateResponse,
    PositionsResponse,
    SummaryResponse,
    TransactionItem,
    TransactionsResponse,
)
from app.services.agent_management_service import AgentManagementService
from app.services.agent_management_service import DuplicateStrategyError, InvalidGeneratedDraftError
from app.services.agent_monitor_stream import AgentMonitorStream


router = APIRouter()


def _validate_asset_class_against_providers(asset_class: str) -> None:
    """Reject agent creation when the selected providers can't serve the asset class."""
    selection = get_provider_selection()
    data_caps = PROVIDER_REGISTRY.get(selection["data_provider"])
    broker_caps = PROVIDER_REGISTRY.get(selection["broker_provider"])
    if data_caps is None or not data_caps.supports_asset_class(asset_class):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Data provider '{selection['data_provider']}' does not support "
                f"'{asset_class}'. Choose a {asset_class}-capable data provider in Settings."
            ),
        )
    if broker_caps is not None and not broker_caps.supports_asset_class(asset_class):
        # Futures with the stock broker (Alpaca) degrades to view-only/no-order.
        # A futures-only broker (TopStepX) on a stock agent is nonsensical -> 400.
        degradable = asset_class == "future" and selection["broker_provider"] == "alpaca"
        if not degradable:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Broker '{selection['broker_provider']}' does not support "
                    f"'{asset_class}'. Select a {asset_class}-capable broker in Settings."
                ),
            )


@router.post("", response_model=AgentResponse)
def create_agent(
    payload: AgentCreateRequest,
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> AgentResponse:
    _validate_asset_class_against_providers(payload.asset_class)
    try:
        record = agent_service.create_agent(
            symbols=payload.symbols,
            timeframe=payload.timeframe,
            risk_profile=payload.risk_profile,
            interval_seconds=payload.interval_seconds,
            name=payload.name,
            generated=payload.generated.model_dump() if payload.generated else None,
            skill_ids=payload.skill_ids,
            asset_class=payload.asset_class,
        )
        return _to_agent_response(record, runtime=_runtime_for(agent_service, record))
    except DuplicateStrategyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidGeneratedDraftError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/generate", response_model=AgentGenerateResponse)
def generate_agent(
    asset_class: str = Query(default="stock"),
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> AgentGenerateResponse:
    resolved = asset_class if asset_class in ("stock", "future") else "stock"
    _validate_asset_class_against_providers(resolved)
    generated = agent_service.generate_agent_draft(asset_class=resolved)
    return AgentGenerateResponse(generated=generated)


@router.get("/generate/stream")
async def generate_agent_stream(
    request: Request,
    asset_class: str = Query(default="stock"),
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> StreamingResponse:
    resolved_asset_class = asset_class if asset_class in ("stock", "future") else "stock"
    _validate_asset_class_against_providers(resolved_asset_class)
    queue: Queue[dict[str, Any]] = Queue(maxsize=400)

    def progress_callback(item: dict[str, Any]) -> None:
        queue.put(
            {
                "event": "research_thought",
                "payload": item,
            }
        )

    def run_generation() -> None:
        try:
            generated = agent_service.generate_agent_draft(
                progress_callback=progress_callback,
                asset_class=resolved_asset_class,
            )
            queue.put({"event": "generate_completed", "payload": {"generated": generated}})
        except Exception as exc:  # pragma: no cover - stream failure path
            queue.put({"event": "generate_failed", "payload": {"error": str(exc)}})

    thread = Thread(target=run_generation, daemon=True, name="agent-generate-stream")
    thread.start()

    async def event_generator() -> AsyncIterator[str]:
        terminal_event_sent = False
        while True:
            if await request.is_disconnected():
                break
            if terminal_event_sent:
                await asyncio.sleep(0.2)
                continue
            try:
                event_payload = await asyncio.to_thread(queue.get, True, 1.0)
            except Empty:
                yield ": keepalive\n\n"
                continue
            yield _to_sse_frame(event_payload)
            if event_payload.get("event") in {"generate_completed", "generate_failed"}:
                terminal_event_sent = True

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/mission/generate", response_model=MissionGenerateResponse)
def generate_mission(
    payload: MissionGenerateRequest,
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> MissionGenerateResponse:
    _validate_asset_class_against_providers(payload.asset_class)
    draft = agent_service.generate_mission_draft(
        symbols=payload.symbols,
        theme=payload.theme,
        timeframe=payload.timeframe,
        risk_profile=payload.risk_profile,
        skill_ids=payload.skill_ids,
        asset_class=payload.asset_class,
    )
    return MissionGenerateResponse(draft=draft)


@router.get("/mission/generate/stream")
async def generate_mission_stream(
    request: Request,
    theme: str | None = Query(default=None),
    symbols: str | None = Query(default=None),
    risk_profile: str | None = Query(default=None),
    timeframe: str | None = Query(default=None),
    asset_class: str = Query(default="stock"),
    skill_ids: str | None = Query(default=None),
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> StreamingResponse:
    resolved_asset_class = asset_class if asset_class in ("stock", "future") else "stock"
    _validate_asset_class_against_providers(resolved_asset_class)
    symbol_list = [s.strip().upper() for s in (symbols or "").split(",") if s.strip()] or None
    skill_list = [s.strip() for s in (skill_ids or "").split(",") if s.strip()] or None
    queue: Queue[dict[str, Any]] = Queue(maxsize=400)

    def progress_callback(item: dict[str, Any]) -> None:
        queue.put({"event": "research_thought", "payload": item})

    def run_generation() -> None:
        try:
            draft = agent_service.generate_mission_draft(
                symbols=symbol_list,
                theme=theme,
                timeframe=timeframe,
                risk_profile=risk_profile,
                skill_ids=skill_list,
                progress_callback=progress_callback,
                asset_class=resolved_asset_class,
            )
            queue.put({"event": "generate_completed", "payload": {"draft": draft}})
        except Exception as exc:  # pragma: no cover - stream failure path
            queue.put({"event": "generate_failed", "payload": {"error": str(exc)}})

    thread = Thread(target=run_generation, daemon=True, name="mission-generate-stream")
    thread.start()

    async def event_generator() -> AsyncIterator[str]:
        terminal_event_sent = False
        while True:
            if await request.is_disconnected():
                break
            if terminal_event_sent:
                await asyncio.sleep(0.2)
                continue
            try:
                event_payload = await asyncio.to_thread(queue.get, True, 1.0)
            except Empty:
                yield ": keepalive\n\n"
                continue
            yield _to_sse_frame(event_payload)
            if event_payload.get("event") in {"generate_completed", "generate_failed"}:
                terminal_event_sent = True

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/mission", response_model=AgentResponse)
def create_mission(
    payload: MissionCreateRequest,
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> AgentResponse:
    draft_asset_class = str(payload.draft.get("asset_class") or "stock")
    _validate_asset_class_against_providers(draft_asset_class)
    record = agent_service.create_mission_agent(
        draft=payload.draft, skill_ids=payload.skill_ids
    )
    return _to_agent_response(record, runtime=_runtime_for(agent_service, record))


@router.post("/{agent_id}/approve", response_model=AgentResponse)
def approve_mission(
    agent_id: str,
    payload: MissionApproveRequest,
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> AgentResponse:
    try:
        record = agent_service.approve_mission(agent_id, window_seconds_override=payload.window_seconds)
        return _to_agent_response(record, runtime=_runtime_for(agent_service, record))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidGeneratedDraftError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{agent_id}/reject", response_model=AgentResponse)
def reject_mission(
    agent_id: str,
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> AgentResponse:
    try:
        record = agent_service.reject_mission(agent_id)
        return _to_agent_response(record, runtime=_runtime_for(agent_service, record))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("", response_model=AgentListResponse)
def list_agents(
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> AgentListResponse:
    return AgentListResponse(
        agents=[
            _to_agent_response(item, runtime=_runtime_for(agent_service, item))
            for item in agent_service.list_agents()
        ]
    )


@router.get("/positions", response_model=PositionsResponse)
def positions(
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> PositionsResponse:
    return PositionsResponse(positions=agent_service.get_live_positions())


@router.get("/summary", response_model=SummaryResponse)
def summary(
    period: str = Query(default="all", pattern="^(day|week|month|all)$"),
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> SummaryResponse:
    result = agent_service.get_live_summary(period=period)
    return SummaryResponse(**result)


@router.get("/transactions", response_model=TransactionsResponse)
def transactions(
    limit: int = Query(default=200, ge=1, le=1000),
    mode: str = Query(default="fills", pattern="^(fills|executed|decisions)$"),
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> TransactionsResponse:
    _ = mode  # Backward-compatible query param; overview now uses Alpaca fills only.
    return TransactionsResponse(
        transactions=[TransactionItem(**item) for item in agent_service.get_live_transactions(limit=limit)]
    )


@router.get("/monitor/stream")
async def monitor_all_agents_stream(
    monitor_stream: AgentMonitorStream = Depends(get_agent_monitor_stream),
) -> StreamingResponse:
    queue = monitor_stream.subscribe(agent_id=None)
    return StreamingResponse(
        _event_generator(queue=queue, monitor_stream=monitor_stream, agent_id=None),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{agent_id}", response_model=AgentResponse)
def get_agent(
    agent_id: str,
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> AgentResponse:
    try:
        record = agent_service.get_agent(agent_id)
        return _to_agent_response(record, runtime=_runtime_for(agent_service, record))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{agent_id}/monitor", response_model=AgentMonitorResponse)
def monitor_agent(
    agent_id: str,
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> AgentMonitorResponse:
    try:
        record = agent_service.get_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    runs = record.get("runs", [])[-20:]
    latest_run = _to_run_detail(runs[-1]) if runs else None
    recent_runs = [_to_run_detail(run) for run in reversed(runs)]
    return AgentMonitorResponse(
        agent=_to_agent_response(record, runtime=_runtime_for(agent_service, record)),
        latest_run=latest_run,
        recent_runs=recent_runs,
    )


@router.get("/{agent_id}/monitor/stream")
async def monitor_agent_stream(
    agent_id: str,
    agent_service: AgentManagementService = Depends(get_agent_management_service),
    monitor_stream: AgentMonitorStream = Depends(get_agent_monitor_stream),
) -> StreamingResponse:
    try:
        agent_service.get_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    queue = monitor_stream.subscribe(agent_id=agent_id)
    return StreamingResponse(
        _event_generator(queue=queue, monitor_stream=monitor_stream, agent_id=agent_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{agent_id}/activate", response_model=AgentResponse)
def activate_agent(
    agent_id: str,
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> AgentResponse:
    try:
        record = agent_service.activate_agent(agent_id)
        return _to_agent_response(record, runtime=_runtime_for(agent_service, record))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{agent_id}/pause", response_model=AgentResponse)
def pause_agent(
    agent_id: str,
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> AgentResponse:
    try:
        record = agent_service.pause_agent(agent_id)
        return _to_agent_response(record, runtime=_runtime_for(agent_service, record))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{agent_id}/skills", response_model=AgentResponse)
def set_agent_skills(
    agent_id: str,
    payload: AgentSkillsUpdateRequest,
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> AgentResponse:
    try:
        record = agent_service.set_agent_skills(agent_id, payload.skill_ids)
        return _to_agent_response(record, runtime=_runtime_for(agent_service, record))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{agent_id}", response_model=AgentDeleteResponse)
def delete_agent(
    agent_id: str,
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> AgentDeleteResponse:
    try:
        agent_service.delete_agent(agent_id)
        return AgentDeleteResponse(agent_id=agent_id, deleted=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _to_agent_response(record: dict, runtime: dict[str, Any]) -> AgentResponse:
    recent_runs = record.get("runs", [])[-5:]
    run_items = [
        AgentRunSummary(
            executed_at=run.get("executed_at", ""),
            spent=float(run.get("spent", 0.0) or 0.0),
            earned=float(run.get("earned", 0.0) or 0.0),
            net_pnl=float(run.get("net_pnl", 0.0) or 0.0),
            trade_count=len(run.get("trades", [])),
        )
        for run in recent_runs
    ]
    return AgentResponse(
        agent_id=record["agent_id"],
        name=record.get("name", ""),
        status=record.get("status", "paused"),
        startup_pause_pending=bool(record.get("startup_pause_pending", False)),
        startup_pause_at=(
            str(record.get("startup_pause_at"))
            if record.get("startup_pause_at") is not None
            else None
        ),
        startup_pause_source_status=(
            str(record.get("startup_pause_source_status"))
            if record.get("startup_pause_source_status") is not None
            else None
        ),
        symbols=record.get("symbols", []),
        ticker=record.get("ticker", (record.get("symbols", [""])[0] if record.get("symbols") else "")),
        timeframe=record.get("timeframe", ""),
        asset_class=record.get("asset_class", "stock") or "stock",
        risk_profile=record.get("risk_profile", "balanced"),
        interval_seconds=int(record.get("interval_seconds", 60)),
        signal_context=record.get("signal_context", []),
        strategy=record.get("strategy", {}),
        strategy_evaluation=record.get(
            "strategy_evaluation",
            {
                "confidence": 0.0,
                "threshold": 0.7,
                "recommendation": "not_recommended",
                "decision_summary": "No evaluator output available.",
                "key_reasons": [],
                "risk_notes": [],
                "high_level_plan": [],
            },
        ),
        execution_plan=record.get("execution_plan", {}),
        research_refs=record.get("research_refs", []),
        created_at=record.get("created_at", ""),
        updated_at=record.get("updated_at", ""),
        last_run_at=record.get("last_run_at"),
        recent_runs=run_items,
        can_run_now=bool(runtime.get("can_run_now", False)),
        next_run_at=runtime.get("next_run_at"),
        next_run_reason=str(runtime.get("next_run_reason", "paused")),
        is_market_open=bool(runtime.get("is_market_open", False)),
        next_market_open=runtime.get("next_market_open"),
        next_market_close=runtime.get("next_market_close"),
        market_status_label=str(runtime.get("market_status_label", "unknown")),
        skill_ids=[str(item) for item in record.get("skill_ids", []) if str(item).strip()],
        mission_status=record.get("mission_status"),
        input_kind=record.get("input_kind"),
        input_theme=record.get("input_theme"),
        theme_summary=str(record.get("theme_summary", "")),
        basket=record.get("basket", []) or [],
        strategy_window_seconds=(
            int(record["strategy_window_seconds"])
            if record.get("strategy_window_seconds") is not None
            else None
        ),
        window_rationale=str(record.get("window_rationale", "")),
        window_started_at=record.get("window_started_at"),
        window_ends_at=record.get("window_ends_at"),
        approved_at=record.get("approved_at"),
        terminal_reason=record.get("terminal_reason"),
        generated_skill_id=record.get("generated_skill_id"),
        decision_log=record.get("decision_log", []) or [],
    )


def _to_run_detail(run: dict[str, Any]) -> AgentRunDetail:
    trades = [
        AgentRunTrade(
            symbol=str(trade.get("symbol", "")).upper(),
            signal=str(trade.get("signal", "HOLD")).upper(),
            side=str(trade.get("side", "hold")).lower(),
            qty=float(trade.get("qty", 0.0) or 0.0),
            latest_close=float(trade.get("latest_close", 0.0) or 0.0),
            value=float(trade.get("value", 0.0) or 0.0),
            order_status=str(trade.get("order_status", "none")),
            timeframe=str(trade.get("timeframe", "")),
            policy_action=(
                str(trade.get("policy_action"))
                if trade.get("policy_action") is not None
                else None
            ),
            policy_reason=(
                str(trade.get("policy_reason"))
                if trade.get("policy_reason") is not None
                else None
            ),
        )
        for trade in run.get("trades", [])
    ]
    return AgentRunDetail(
        executed_at=str(run.get("executed_at", "")),
        spent=float(run.get("spent", 0.0) or 0.0),
        earned=float(run.get("earned", 0.0) or 0.0),
        net_pnl=float(run.get("net_pnl", 0.0) or 0.0),
        trades=trades,
    )


def _to_sse_frame(event_payload: dict[str, Any]) -> str:
    event_name = str(event_payload.get("event", "message"))
    encoded = json.dumps(event_payload, separators=(",", ":"))
    return f"event: {event_name}\ndata: {encoded}\n\n"


async def _event_generator(
    queue: Queue[dict[str, Any]],
    monitor_stream: AgentMonitorStream,
    agent_id: str | None,
) -> AsyncIterator[str]:
    try:
        while True:
            try:
                event_payload = await asyncio.to_thread(queue.get, True, 15.0)
            except Empty:
                yield ": keepalive\n\n"
                continue
            yield _to_sse_frame(event_payload)
    finally:
        monitor_stream.unsubscribe(queue=queue, agent_id=agent_id)


def _runtime_for(agent_service: AgentManagementService, record: dict[str, Any]) -> dict[str, Any]:
    if hasattr(agent_service, "get_agent_runtime"):
        runtime = agent_service.get_agent_runtime(record)
        if isinstance(runtime, dict):
            return runtime
    return {
        "can_run_now": record.get("status") == "active",
        "next_run_at": None,
        "next_run_reason": "paused" if record.get("status") != "active" else "ready",
        "is_market_open": True,
        "next_market_open": None,
        "next_market_close": None,
        "market_status_label": "market_unknown",
    }
