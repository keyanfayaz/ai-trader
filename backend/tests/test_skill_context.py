from __future__ import annotations

from pathlib import Path

import pytest

from app.repositories.skill_store import SkillStore
from app.services.skill_context import build_skill_prompt_block

SKILL_MD = """---
name: ctx-skill
description: Context skill for prompt block tests.
---

# Strategy Rules

Only enter on confirmed breakout.
"""


@pytest.fixture
def store(tmp_path: Path) -> SkillStore:
    return SkillStore(base_dir=str(tmp_path / "skills"))


def test_returns_none_without_skills(store: SkillStore):
    assert build_skill_prompt_block(store, []) is None
    assert build_skill_prompt_block(store, None) is None
    assert build_skill_prompt_block(None, ["x"]) is None


def test_block_includes_name_description_body_and_reference(store: SkillStore):
    meta = store.create_from_upload(
        files=[
            ("SKILL.md", SKILL_MD.encode()),
            ("references/extra.md", b"Extra reference detail."),
        ]
    )
    block = build_skill_prompt_block(store, [meta["id"]])
    assert block is not None
    assert "ctx-skill" in block
    assert "Context skill for prompt block tests." in block
    assert "Only enter on confirmed breakout." in block
    assert "Extra reference detail." in block
    assert "NOT executed" in block  # preamble mentions scripts are not executed


def test_missing_skill_id_is_skipped(store: SkillStore):
    meta = store.create_from_editor(markdown=SKILL_MD)
    block = build_skill_prompt_block(store, ["00000000-0000-0000-0000-000000000000", meta["id"]])
    assert block is not None
    assert "ctx-skill" in block


def test_budget_truncation(store: SkillStore, monkeypatch):
    import app.services.skill_context as ctx

    monkeypatch.setattr(ctx, "SKILL_CONTEXT_MAX_BYTES", 200)
    big_body = SKILL_MD + ("\nfiller line" * 500)
    meta = store.create_from_editor(markdown=big_body)
    block = build_skill_prompt_block(store, [meta["id"]])
    assert block is not None
    assert "[truncated]" in block
