from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.repositories.skill_store import (
    MAX_SKILL_FILES,
    SkillNotFoundError,
    SkillStore,
    SkillVersionConflictError,
)
from app.services.skill_spec import SkillValidationError


SKILL_MD = """---
name: test-skill
description: A test skill used for unit testing the skill store.
---

# Test Skill

Body content.
"""


@pytest.fixture
def store(tmp_path: Path) -> SkillStore:
    return SkillStore(base_dir=str(tmp_path / "skills"))


def test_create_from_editor_and_get(store: SkillStore):
    meta = store.create_from_editor(markdown=SKILL_MD)
    assert meta["name"] == "test-skill"
    detail = store.get_skill(meta["id"])
    assert detail["markdown"].strip().startswith("---")
    assert [f["path"] for f in detail["files"]] == ["SKILL.md"]


def test_create_from_editor_invalid_raises(store: SkillStore):
    with pytest.raises(SkillValidationError):
        store.create_from_editor(markdown="no frontmatter")


def test_list_skills(store: SkillStore):
    store.create_from_editor(markdown=SKILL_MD)
    store.create_from_editor(markdown=SKILL_MD)
    assert len(store.list_skills()) == 2


def test_upload_zip_with_nested_top_dir_and_references(store: SkillStore):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bundle-root/SKILL.md", SKILL_MD)
        archive.writestr("bundle-root/references/notes.md", "# Notes\nreference text")
    meta = store.create_from_upload(zip_bytes=buffer.getvalue())
    detail = store.get_skill(meta["id"])
    paths = sorted(f["path"] for f in detail["files"])
    assert paths == ["SKILL.md", "references/notes.md"]
    assert store.read_bundle_file(meta["id"], "references/notes.md").startswith("# Notes")


def test_zip_slip_rejected(store: SkillStore):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", SKILL_MD)
        archive.writestr("../evil.txt", "pwn")
    with pytest.raises(SkillValidationError):
        store.create_from_upload(zip_bytes=buffer.getvalue())


def test_upload_requires_skill_md(store: SkillStore):
    with pytest.raises(SkillValidationError):
        store.create_from_upload(files=[("readme.txt", b"hello")])


def test_file_count_cap(store: SkillStore):
    files = [("SKILL.md", SKILL_MD.encode())]
    files += [(f"extra/file{i}.txt", b"x") for i in range(MAX_SKILL_FILES + 5)]
    with pytest.raises(SkillValidationError):
        store.create_from_upload(files=files)


def test_update_version_conflict(store: SkillStore):
    meta = store.create_from_editor(markdown=SKILL_MD)
    with pytest.raises(SkillVersionConflictError):
        store.update_skill_md(meta["id"], SKILL_MD.replace("Body", "Changed"), expected_version="deadbeef")
    updated = store.update_skill_md(
        meta["id"], SKILL_MD.replace("Body", "Changed"), expected_version=meta["version"]
    )
    assert "Changed" in updated["markdown"]
    assert updated["version"] != meta["version"]


def test_delete_skill(store: SkillStore):
    meta = store.create_from_editor(markdown=SKILL_MD)
    store.delete_skill(meta["id"])
    with pytest.raises(SkillNotFoundError):
        store.get_skill(meta["id"])


def test_invalid_skill_id(store: SkillStore):
    with pytest.raises(SkillNotFoundError):
        store.get_skill("../etc/passwd")
