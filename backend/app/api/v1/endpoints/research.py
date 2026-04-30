from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_market_research_service, get_research_store
from app.api.schemas import (
    ResearchRecordDeleteResponse,
    ResearchRecordDetailResponse,
    ResearchRecordsDeleteRequest,
    ResearchRecordsDeleteResponse,
    ResearchRecordTreeResponse,
    ResearchRecordUpdateRequest,
    ResearchRecordUpdateResponse,
    StrategyResearchRequest,
    StrategyResearchResponse,
)
from app.repositories.markdown_research_store import MarkdownResearchStore
from app.services.market_research_service import MarketResearchService


router = APIRouter()


@router.post("/strategy", response_model=StrategyResearchResponse)
def strategy(
    payload: StrategyResearchRequest,
    research_service: MarketResearchService = Depends(get_market_research_service),
) -> StrategyResearchResponse:
    result = research_service.build_strategy(
        symbols=payload.symbols,
        timeframe=payload.timeframe,
        risk_profile=payload.risk_profile,
    )
    return StrategyResearchResponse(
        symbols=result.symbols,
        ticker=result.ticker,
        timeframe=result.timeframe,
        risk_profile=result.risk_profile,
        signal_context=result.signal_context,
        strategy=result.strategy,
        evaluation=result.evaluation,
        research_refs=result.research_refs,
        research_thoughts=result.research_thoughts,
        quality_findings=result.quality_findings,
    )


@router.get("/records", response_model=ResearchRecordTreeResponse)
def list_records(
    research_store: MarkdownResearchStore = Depends(get_research_store),
) -> ResearchRecordTreeResponse:
    return ResearchRecordTreeResponse(folders=research_store.list_record_tree())


@router.get("/records/{record_id}", response_model=ResearchRecordDetailResponse)
def get_record(
    record_id: str,
    research_store: MarkdownResearchStore = Depends(get_research_store),
) -> ResearchRecordDetailResponse:
    try:
        return ResearchRecordDetailResponse(**research_store.get_record(record_id=record_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/records/{record_id}", response_model=ResearchRecordUpdateResponse)
def update_record(
    record_id: str,
    payload: ResearchRecordUpdateRequest,
    research_store: MarkdownResearchStore = Depends(get_research_store),
) -> ResearchRecordUpdateResponse:
    try:
        record = research_store.update_record(
            record_id=record_id,
            markdown=payload.markdown,
            expected_version=payload.expected_version,
        )
        return ResearchRecordUpdateResponse(record=ResearchRecordDetailResponse(**record))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        if "changed since it was loaded" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/records/{record_id}", response_model=ResearchRecordDeleteResponse)
def delete_record(
    record_id: str,
    research_store: MarkdownResearchStore = Depends(get_research_store),
) -> ResearchRecordDeleteResponse:
    try:
        deleted = research_store.delete_record(record_id=record_id)
        return ResearchRecordDeleteResponse(**deleted)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/records/delete", response_model=ResearchRecordsDeleteResponse)
def delete_records(
    payload: ResearchRecordsDeleteRequest,
    research_store: MarkdownResearchStore = Depends(get_research_store),
) -> ResearchRecordsDeleteResponse:
    has_record_ids = len(payload.record_ids) > 0
    has_folder = payload.folder is not None and payload.folder.strip() != ""
    if has_record_ids and has_folder:
        raise HTTPException(status_code=400, detail="Provide either record_ids or folder, not both.")
    if not has_record_ids and not has_folder:
        raise HTTPException(status_code=400, detail="Provide record_ids or folder.")

    try:
        if has_folder:
            deleted = research_store.delete_folder(folder=str(payload.folder))
            return ResearchRecordsDeleteResponse(
                deleted_count=int(deleted.get("deleted_count", 0)),
                deleted_ids=list(deleted.get("deleted_ids", [])),
                missing_ids=[],
                folder=str(deleted.get("folder", "")),
            )
        deleted = research_store.delete_records(record_ids=payload.record_ids)
        return ResearchRecordsDeleteResponse(**deleted)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
