from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml


_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_MAX_NAME_LEN = 64
_MAX_DESCRIPTION_LEN = 1024
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


class SkillValidationError(ValueError):
    """Raised when a SKILL.md file does not satisfy the Agent Skills spec."""


@dataclass(slots=True)
class ParsedSkill:
    name: str
    description: str
    body: str
    license: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_skill_md(text: str) -> ParsedSkill:
    """Parse and validate a SKILL.md document per the agentskills.io spec."""
    if not text or not text.strip():
        raise SkillValidationError("SKILL.md is empty.")

    match = _FRONTMATTER_RE.match(text.lstrip("﻿"))
    if match is None:
        raise SkillValidationError(
            "SKILL.md must start with a YAML frontmatter block delimited by '---' lines."
        )

    raw_frontmatter, body = match.group(1), match.group(2)
    try:
        frontmatter = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError as exc:
        raise SkillValidationError(f"SKILL.md frontmatter is not valid YAML: {exc}") from exc

    if not isinstance(frontmatter, dict):
        raise SkillValidationError("SKILL.md frontmatter must be a mapping of keys to values.")

    name = str(frontmatter.get("name", "")).strip()
    if not name:
        raise SkillValidationError("SKILL.md frontmatter is missing required key 'name'.")
    if len(name) > _MAX_NAME_LEN:
        raise SkillValidationError(f"Skill 'name' must be at most {_MAX_NAME_LEN} characters.")
    if not _NAME_RE.match(name):
        raise SkillValidationError(
            "Skill 'name' must be lowercase letters, digits, and single hyphens "
            "(e.g. 'momentum-breakout')."
        )

    description = str(frontmatter.get("description", "")).strip()
    if not description:
        raise SkillValidationError("SKILL.md frontmatter is missing required key 'description'.")
    if len(description) > _MAX_DESCRIPTION_LEN:
        raise SkillValidationError(
            f"Skill 'description' must be at most {_MAX_DESCRIPTION_LEN} characters."
        )

    allowed_tools_raw = frontmatter.get("allowed-tools", frontmatter.get("allowed_tools"))
    allowed_tools: list[str] = []
    if isinstance(allowed_tools_raw, list):
        allowed_tools = [str(item).strip() for item in allowed_tools_raw if str(item).strip()]
    elif isinstance(allowed_tools_raw, str) and allowed_tools_raw.strip():
        allowed_tools = [part.strip() for part in allowed_tools_raw.split(",") if part.strip()]

    license_value = frontmatter.get("license")
    metadata = frontmatter.get("metadata")
    version_value = frontmatter.get("version")
    if version_value is None and isinstance(metadata, dict):
        version_value = metadata.get("version")

    return ParsedSkill(
        name=name,
        description=description,
        body=body.strip("\n"),
        license=str(license_value).strip() if license_value not in (None, "") else None,
        allowed_tools=allowed_tools,
        version=str(version_value).strip() if version_value not in (None, "") else None,
        metadata=metadata if isinstance(metadata, dict) else {},
    )
