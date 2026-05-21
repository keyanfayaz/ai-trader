"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import {
  createSkillFromEditor,
  deleteSkill,
  getSkill,
  listSkills,
  updateSkill,
  uploadSkill,
} from "@/lib/api";
import type { SkillDetail, SkillSummary } from "@/lib/types";

const NEW_SKILL_TEMPLATE = `---
name: my-strategy-skill
description: When to use this skill and what trading edge it encodes.
---

# My Strategy Skill

Describe the strategy rules, entry/exit logic, risk constraints, and any
heuristics the agent should follow when generating or refreshing its strategy.
`;

function toLocalTimestamp(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [mode, setMode] = useState<"view" | "edit" | "new">("view");
  const [draftMarkdown, setDraftMarkdown] = useState("");
  const [loadingList, setLoadingList] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const folderInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const input = folderInputRef.current;
    if (input) {
      input.setAttribute("webkitdirectory", "");
      input.setAttribute("directory", "");
    }
  }, []);

  async function loadList(nextSelected?: string) {
    setLoadingList(true);
    try {
      const response = await listSkills();
      setSkills(response.skills);
      const target = nextSelected ?? selectedId;
      if (target && response.skills.some((skill) => skill.id === target)) {
        setSelectedId(target);
      } else if (response.skills.length > 0) {
        setSelectedId(response.skills[0].id);
      } else {
        setSelectedId(null);
        setDetail(null);
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load skills.");
    } finally {
      setLoadingList(false);
    }
  }

  useEffect(() => {
    void loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedId || mode === "new") {
      return;
    }
    setLoadingDetail(true);
    setError("");
    void (async () => {
      try {
        const response = await getSkill(selectedId);
        setDetail(response);
        setDraftMarkdown(response.markdown);
        setMode("view");
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : "Unable to load skill.");
      } finally {
        setLoadingDetail(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  function buildFormData(fileList: FileList): FormData {
    const formData = new FormData();
    const files = Array.from(fileList);
    if (files.length === 1 && files[0].name.toLowerCase().endsWith(".zip")) {
      formData.append("file", files[0]);
      return formData;
    }
    const paths: string[] = [];
    for (const file of files) {
      const relativePath =
        (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      formData.append("files", file);
      paths.push(relativePath);
    }
    formData.append("paths", JSON.stringify(paths));
    return formData;
  }

  async function handleUpload(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) {
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await uploadSkill(buildFormData(fileList));
      setNotice(`Uploaded skill "${created.name}".`);
      await loadList(created.id);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Skill upload failed.");
    } finally {
      setBusy(false);
    }
  }

  function startNewSkill() {
    setMode("new");
    setDetail(null);
    setSelectedId(null);
    setDraftMarkdown(NEW_SKILL_TEMPLATE);
    setError("");
    setNotice("");
  }

  async function saveSkill() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (mode === "new") {
        const created = await createSkillFromEditor(draftMarkdown);
        setNotice(`Created skill "${created.name}".`);
        await loadList(created.id);
      } else if (detail) {
        const updated = await updateSkill(detail.id, draftMarkdown, detail.version);
        setDetail(updated);
        setNotice(`Saved skill "${updated.name}".`);
        await loadList(updated.id);
      }
      setMode("view");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save skill.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!detail) {
      return;
    }
    if (!window.confirm(`Delete skill "${detail.name}"? It will be unbound from all agents.`)) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await deleteSkill(detail.id);
      setNotice(`Deleted skill "${detail.name}".`);
      setDetail(null);
      await loadList();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete skill.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Skills</h2>
        <button type="button" disabled={busy} onClick={startNewSkill}>
          Build new skill
        </button>
      </div>
      <p className="muted">
        Skills are reusable strategy playbooks (Agent Skills spec). Bound skills are injected as
        instructional context into an agent&apos;s strategy generation and runtime strategy
        refresh. Bundled scripts are stored as read-only reference and are not executed.
      </p>
      {error ? <p className="error">{error}</p> : null}
      {notice ? <p className="success">{notice}</p> : null}

      <div className="form-grid" style={{ maxWidth: "none" }}>
        <label>
          <span>Upload zip, file(s), or a folder</span>
          <input
            type="file"
            multiple
            disabled={busy}
            onChange={(event) => void handleUpload(event.target.files)}
          />
        </label>
        <label>
          <span>Upload a skill folder</span>
          <input
            ref={folderInputRef}
            type="file"
            multiple
            disabled={busy}
            onChange={(event) => void handleUpload(event.target.files)}
          />
        </label>
      </div>

      <div className="research-layout">
        <div className="research-tree">
          <div className="research-tree-header">
            <h3>Library</h3>
            <button type="button" disabled={loadingList} onClick={() => void loadList()}>
              Refresh
            </button>
          </div>
          <div className="research-files">
            {skills.length === 0 ? (
              <p className="muted">{loadingList ? "Loading…" : "No skills yet."}</p>
            ) : (
              skills.map((skill) => (
                <div
                  key={skill.id}
                  className={skill.id === selectedId ? "research-file active" : "research-file"}
                >
                  <button
                    type="button"
                    className="research-file-open"
                    onClick={() => {
                      setMode("view");
                      setSelectedId(skill.id);
                    }}
                  >
                    <strong>{skill.name}</strong>
                    <span>{skill.description}</span>
                    <span>
                      {skill.source} · {skill.file_count} file(s) ·{" "}
                      {toLocalTimestamp(skill.updated_at)}
                    </span>
                  </button>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="research-document">
          {mode === "new" ? (
            <>
              <div className="research-document-header">
                <h3>New skill (SKILL.md)</h3>
                <div className="row-actions">
                  <button type="button" disabled={busy} onClick={() => void saveSkill()}>
                    Create
                  </button>
                  <button type="button" disabled={busy} onClick={() => void loadList()}>
                    Cancel
                  </button>
                </div>
              </div>
              <textarea
                className="research-editor"
                value={draftMarkdown}
                onChange={(event) => setDraftMarkdown(event.target.value)}
              />
            </>
          ) : loadingDetail ? (
            <p className="muted">Loading skill…</p>
          ) : detail ? (
            <>
              <div className="research-document-header">
                <h3>{detail.name}</h3>
                <div className="row-actions">
                  {mode === "edit" ? (
                    <>
                      <button type="button" disabled={busy} onClick={() => void saveSkill()}>
                        Save
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          setDraftMarkdown(detail.markdown);
                          setMode("view");
                        }}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      <button type="button" disabled={busy} onClick={() => setMode("edit")}>
                        Edit SKILL.md
                      </button>
                      <button type="button" disabled={busy} onClick={() => void handleDelete()}>
                        Delete
                      </button>
                    </>
                  )}
                </div>
              </div>
              <p className="muted">
                {detail.description} · version {detail.version}
              </p>
              {detail.files.length > 0 ? (
                <p className="muted">
                  Bundled files: {detail.files.map((file) => file.path).join(", ")}
                </p>
              ) : null}
              {mode === "edit" ? (
                <textarea
                  className="research-editor"
                  value={draftMarkdown}
                  onChange={(event) => setDraftMarkdown(event.target.value)}
                />
              ) : (
                <div className="research-preview">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
                    {detail.markdown}
                  </ReactMarkdown>
                </div>
              )}
            </>
          ) : (
            <p className="muted">Select a skill or upload/build a new one.</p>
          )}
        </div>
      </div>
    </section>
  );
}
