from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import Any

from app.repositories.skill_store import SkillStore


LOGGER = logging.getLogger(__name__)

SKILL_CONTEXT_MAX_BYTES = 30 * 1024
_TEXT_REFERENCE_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}
_CODE_REFERENCE_SUFFIXES = {".py", ".sh"}

_PREAMBLE = (
    "The following Agent Skills are authoritative strategy playbooks selected by the user "
    "for this trading agent. Treat their instructions as higher priority than your generic "
    "defaults when forming the strategy and evaluation. Bundled scripts are provided as "
    "READ-ONLY reference only and are NOT executed."
)


def build_skill_prompt_block(
    skill_store: SkillStore | None,
    skill_ids: list[str] | None,
) -> str | None:
    """Compose an instructional prompt block from the agent's bound skills.

    Returns None when there are no usable skills so callers stay backward-compatible.
    """
    if skill_store is None or not skill_ids:
        return None

    sections: list[str] = [_PREAMBLE]
    budget = SKILL_CONTEXT_MAX_BYTES
    included_any = False

    for skill_id in skill_ids:
        if budget <= 0:
            break
        try:
            skill = skill_store.get_skill(skill_id)
        except Exception as exc:  # missing/invalid skill should not break generation
            LOGGER.warning("Skipping skill %s in prompt context: %s", skill_id, exc)
            continue

        name = str(skill.get("name", "skill"))
        description = str(skill.get("description", "")).strip()
        body = str(skill.get("markdown", "")).strip()
        header = f"\n## Skill: {name}\n{description}\n\n{body}\n"
        chunk, budget = _take(header, budget)
        sections.append(chunk)
        included_any = True

        for file_entry in skill.get("files", []):
            if budget <= 0:
                break
            rel_path = str(file_entry.get("path", ""))
            if rel_path == "SKILL.md":
                continue
            suffix = PurePosixPath(rel_path).suffix.lower()
            is_text = suffix in _TEXT_REFERENCE_SUFFIXES or rel_path.startswith("references/")
            is_code = suffix in _CODE_REFERENCE_SUFFIXES
            if not (is_text or is_code):
                continue
            try:
                content = skill_store.read_bundle_file(skill_id, rel_path)
            except Exception:
                continue
            if is_code:
                lang = suffix.lstrip(".")
                block = (
                    f"\n### Reference (read-only, NOT executed): {rel_path}\n"
                    f"```{lang}\n{content}\n```\n"
                )
            else:
                block = f"\n### Reference: {rel_path}\n{content}\n"
            chunk, budget = _take(block, budget)
            sections.append(chunk)

    if not included_any:
        return None
    return "".join(sections).strip()


def _take(text: str, budget: int) -> tuple[str, int]:
    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text, budget - len(encoded)
    truncated = encoded[: max(budget, 0)].decode("utf-8", errors="ignore")
    return truncated + "\n…[truncated]\n", 0


def normalize_skill_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    return seen
