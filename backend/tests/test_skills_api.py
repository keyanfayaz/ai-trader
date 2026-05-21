from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient


SKILL_MD = """---
name: api-skill
description: Skill exercised through the HTTP API.
---

# API Skill

Trade only with confirmation.
"""


def test_editor_crud_flow(api_client: TestClient):
    created = api_client.post("/api/v1/skills/editor", json={"markdown": SKILL_MD})
    assert created.status_code == 201
    skill = created.json()
    assert skill["name"] == "api-skill"
    skill_id = skill["id"]

    listed = api_client.get("/api/v1/skills")
    assert listed.status_code == 200
    assert any(item["id"] == skill_id for item in listed.json()["skills"])

    detail = api_client.get(f"/api/v1/skills/{skill_id}")
    assert detail.status_code == 200
    assert "API Skill" in detail.json()["markdown"]

    bad = api_client.post("/api/v1/skills/editor", json={"markdown": "no frontmatter"})
    assert bad.status_code == 400

    updated = api_client.patch(
        f"/api/v1/skills/{skill_id}",
        json={"markdown": SKILL_MD.replace("confirmation", "volume"), "expected_version": skill["version"]},
    )
    assert updated.status_code == 200
    assert "volume" in updated.json()["markdown"]

    conflict = api_client.patch(
        f"/api/v1/skills/{skill_id}",
        json={"markdown": SKILL_MD, "expected_version": "stale-version"},
    )
    assert conflict.status_code == 409


def test_zip_upload(api_client: TestClient):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("root/SKILL.md", SKILL_MD)
        archive.writestr("root/references/r.md", "ref")
    resp = api_client.post(
        "/api/v1/skills",
        files={"file": ("skill.zip", buffer.getvalue(), "application/zip")},
    )
    assert resp.status_code == 201
    assert sorted(f["path"] for f in resp.json()["files"]) == ["SKILL.md", "references/r.md"]


def test_multi_file_upload_with_paths(api_client: TestClient):
    resp = api_client.post(
        "/api/v1/skills",
        files=[
            ("files", ("SKILL.md", SKILL_MD, "text/markdown")),
            ("files", ("guide.md", b"# Guide", "text/markdown")),
        ],
        data={"paths": json.dumps(["pkg/SKILL.md", "pkg/references/guide.md"])},
    )
    assert resp.status_code == 201
    assert sorted(f["path"] for f in resp.json()["files"]) == ["SKILL.md", "references/guide.md"]


def test_bind_skills_to_agent_and_unbind_on_delete(api_client: TestClient):
    created = api_client.post("/api/v1/skills/editor", json={"markdown": SKILL_MD}).json()
    skill_id = created["id"]

    bound = api_client.put("/api/v1/agents/agent-1/skills", json={"skill_ids": [skill_id]})
    assert bound.status_code == 200
    assert bound.json()["skill_ids"] == [skill_id]

    deleted = api_client.delete(f"/api/v1/skills/{skill_id}")
    assert deleted.status_code == 200

    agent = api_client.get("/api/v1/agents/agent-1").json()
    assert skill_id not in agent["skill_ids"]
