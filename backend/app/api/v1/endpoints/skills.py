from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.dependencies import get_agent_management_service, get_skill_store
from app.api.schemas import (
    SkillDeleteResponse,
    SkillDetailResponse,
    SkillEditorCreateRequest,
    SkillFileResponse,
    SkillListResponse,
    SkillSummary,
    SkillUpdateRequest,
)
from app.repositories.skill_store import (
    SkillNotFoundError,
    SkillStore,
    SkillVersionConflictError,
)
from app.services.agent_management_service import AgentManagementService
from app.services.skill_spec import SkillValidationError


router = APIRouter()


@router.get("", response_model=SkillListResponse)
def list_skills(
    skill_store: SkillStore = Depends(get_skill_store),
) -> SkillListResponse:
    return SkillListResponse(skills=[SkillSummary(**item) for item in skill_store.list_skills()])


@router.post("", response_model=SkillDetailResponse, status_code=201)
async def upload_skill(
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    paths: str | None = Form(default=None),
    skill_store: SkillStore = Depends(get_skill_store),
) -> SkillDetailResponse:
    try:
        if file is not None and (file.filename or "").lower().endswith(".zip"):
            meta = skill_store.create_from_upload(zip_bytes=await file.read())
        else:
            uploads = list(files or [])
            if file is not None:
                uploads = [file, *uploads]
            if not uploads:
                raise SkillValidationError("No files were uploaded.")
            rel_paths: list[str] | None = None
            if paths:
                try:
                    parsed = json.loads(paths)
                except json.JSONDecodeError as exc:
                    raise SkillValidationError("`paths` must be a JSON array.") from exc
                if isinstance(parsed, list):
                    rel_paths = [str(p) for p in parsed]
            collected: list[tuple[str, bytes]] = []
            for index, upload in enumerate(uploads):
                rel = (
                    rel_paths[index]
                    if rel_paths is not None and index < len(rel_paths)
                    else (upload.filename or f"file-{index}")
                )
                collected.append((rel, await upload.read()))
            meta = skill_store.create_from_upload(files=collected)
        return SkillDetailResponse(**skill_store.get_skill(meta["id"]))
    except SkillValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/editor", response_model=SkillDetailResponse, status_code=201)
def create_skill_from_editor(
    payload: SkillEditorCreateRequest,
    skill_store: SkillStore = Depends(get_skill_store),
) -> SkillDetailResponse:
    try:
        meta = skill_store.create_from_editor(markdown=payload.markdown, name=payload.name)
        return SkillDetailResponse(**skill_store.get_skill(meta["id"]))
    except SkillValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{skill_id}", response_model=SkillDetailResponse)
def get_skill(
    skill_id: str,
    skill_store: SkillStore = Depends(get_skill_store),
) -> SkillDetailResponse:
    try:
        return SkillDetailResponse(**skill_store.get_skill(skill_id))
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{skill_id}/file", response_model=SkillFileResponse)
def get_skill_file(
    skill_id: str,
    path: str,
    skill_store: SkillStore = Depends(get_skill_store),
) -> SkillFileResponse:
    try:
        return SkillFileResponse(path=path, content=skill_store.read_bundle_file(skill_id, path))
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{skill_id}", response_model=SkillDetailResponse)
def update_skill(
    skill_id: str,
    payload: SkillUpdateRequest,
    skill_store: SkillStore = Depends(get_skill_store),
) -> SkillDetailResponse:
    try:
        return SkillDetailResponse(
            **skill_store.update_skill_md(
                skill_id=skill_id,
                markdown=payload.markdown,
                expected_version=payload.expected_version,
            )
        )
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillVersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SkillValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{skill_id}", response_model=SkillDeleteResponse)
def delete_skill(
    skill_id: str,
    skill_store: SkillStore = Depends(get_skill_store),
    agent_service: AgentManagementService = Depends(get_agent_management_service),
) -> SkillDeleteResponse:
    try:
        result = skill_store.delete_skill(skill_id)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    for agent in agent_service.list_agents():
        bound = [str(item) for item in agent.get("skill_ids", []) if str(item).strip()]
        if skill_id in bound:
            agent_service.set_agent_skills(
                agent["agent_id"], [item for item in bound if item != skill_id]
            )
    return SkillDeleteResponse(**result)
