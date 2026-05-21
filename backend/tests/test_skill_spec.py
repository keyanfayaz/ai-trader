from __future__ import annotations

import pytest

from app.services.skill_spec import SkillValidationError, parse_skill_md


VALID = """---
name: momentum-breakout
description: Use when trading liquid momentum breakouts on intraday timeframes.
license: MIT
allowed-tools: [read, search]
metadata:
  version: 1.2.0
---

# Momentum Breakout

Enter on confirmed breakout with volume expansion.
"""


def test_parse_valid_skill():
    parsed = parse_skill_md(VALID)
    assert parsed.name == "momentum-breakout"
    assert parsed.description.startswith("Use when trading")
    assert parsed.license == "MIT"
    assert parsed.allowed_tools == ["read", "search"]
    assert parsed.version == "1.2.0"
    assert "Momentum Breakout" in parsed.body


def test_missing_frontmatter():
    with pytest.raises(SkillValidationError):
        parse_skill_md("# Just markdown, no frontmatter\n")


def test_missing_name():
    text = "---\ndescription: something\n---\nbody\n"
    with pytest.raises(SkillValidationError):
        parse_skill_md(text)


def test_missing_description():
    text = "---\nname: my-skill\n---\nbody\n"
    with pytest.raises(SkillValidationError):
        parse_skill_md(text)


@pytest.mark.parametrize("bad_name", ["My-Skill", "skill_underscore", "a" * 65, "-leading", "trailing-"])
def test_invalid_name(bad_name: str):
    text = f"---\nname: {bad_name}\ndescription: ok\n---\nbody\n"
    with pytest.raises(SkillValidationError):
        parse_skill_md(text)


def test_description_too_long():
    text = f"---\nname: ok-skill\ndescription: {'x' * 1100}\n---\nbody\n"
    with pytest.raises(SkillValidationError):
        parse_skill_md(text)
