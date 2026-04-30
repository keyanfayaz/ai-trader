"use client";

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import {
  deleteResearchFolder,
  deleteResearchRecord,
  deleteResearchRecords,
  getResearchRecord,
  listResearchRecords,
  updateResearchRecord,
} from "@/lib/api";
import type { ResearchRecordDetail, ResearchRecordFolder } from "@/lib/types";

function toLocalTimestamp(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

export default function ResearchPage() {
  const [folders, setFolders] = useState<ResearchRecordFolder[]>([]);
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [record, setRecord] = useState<ResearchRecordDetail | null>(null);
  const [draftMarkdown, setDraftMarkdown] = useState<string>("");
  const [editing, setEditing] = useState(false);
  const [loadingTree, setLoadingTree] = useState(false);
  const [loadingRecord, setLoadingRecord] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string>("");
  const [notice, setNotice] = useState<string>("");
  const [selectedRecordIds, setSelectedRecordIds] = useState<Record<string, boolean>>({});

  async function loadTree() {
    setLoadingTree(true);
    try {
      const response = await listResearchRecords();
      setFolders(response.folders);
      setSelectedRecordIds((previous) => {
        const allowed = new Set(response.folders.flatMap((folder) => folder.records.map((item) => item.id)));
        const next: Record<string, boolean> = {};
        for (const [id, checked] of Object.entries(previous)) {
          if (allowed.has(id) && checked) {
            next[id] = true;
          }
        }
        return next;
      });
      setExpandedFolders((previous) => {
        const next: Record<string, boolean> = {};
        for (const folder of response.folders) {
          next[folder.folder] = previous[folder.folder] ?? true;
        }
        return next;
      });
      if (!selectedId && response.folders.length > 0 && response.folders[0].records.length > 0) {
        setSelectedId(response.folders[0].records[0].id);
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load research records.");
    } finally {
      setLoadingTree(false);
    }
  }

  useEffect(() => {
    void loadTree();
    // We intentionally load tree only on initial mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setRecord(null);
      setDraftMarkdown("");
      return;
    }
    let active = true;
    setLoadingRecord(true);
    setError("");
    setNotice("");
    void getResearchRecord(selectedId)
      .then((response) => {
        if (!active) {
          return;
        }
        setRecord(response);
        setDraftMarkdown(response.markdown);
        setEditing(false);
      })
      .catch((loadError) => {
        if (!active) {
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "Unable to load research document.");
      })
      .finally(() => {
        if (active) {
          setLoadingRecord(false);
        }
      });
    return () => {
      active = false;
    };
  }, [selectedId]);

  const hasRecords = useMemo(() => folders.some((folder) => folder.records.length > 0), [folders]);

  function toggleFolder(folder: string) {
    setExpandedFolders((previous) => ({ ...previous, [folder]: !previous[folder] }));
  }

  const selectedIds = useMemo(
    () => Object.entries(selectedRecordIds).filter(([, checked]) => checked).map(([recordId]) => recordId),
    [selectedRecordIds],
  );

  function toggleRecordSelection(recordId: string) {
    setSelectedRecordIds((previous) => ({ ...previous, [recordId]: !previous[recordId] }));
  }

  function toggleFolderSelection(folder: ResearchRecordFolder) {
    const allChecked = folder.records.every((item) => selectedRecordIds[item.id]);
    setSelectedRecordIds((previous) => {
      const next = { ...previous };
      for (const item of folder.records) {
        next[item.id] = !allChecked;
      }
      return next;
    });
  }

  async function onSave() {
    if (!selectedId || !record) {
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const updated = await updateResearchRecord(selectedId, draftMarkdown, record.version);
      setRecord(updated.record);
      setDraftMarkdown(updated.record.markdown);
      setEditing(false);
      setNotice("Saved research markdown.");
      await loadTree();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save research markdown.");
    } finally {
      setSaving(false);
    }
  }

  async function onDeleteCurrent() {
    if (!record) {
      return;
    }
    const confirmed = window.confirm(`Delete "${record.file_name}" permanently?`);
    if (!confirmed) {
      return;
    }
    setDeleting(true);
    setError("");
    setNotice("");
    try {
      await deleteResearchRecord(record.id);
      setSelectedRecordIds((previous) => {
        const next = { ...previous };
        delete next[record.id];
        return next;
      });
      setRecord(null);
      setDraftMarkdown("");
      setSelectedId(null);
      setEditing(false);
      setNotice("Deleted research document.");
      await loadTree();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete research document.");
    } finally {
      setDeleting(false);
    }
  }

  async function onDeleteSelected() {
    if (selectedIds.length === 0) {
      return;
    }
    const confirmed = window.confirm(`Delete ${selectedIds.length} selected research documents permanently?`);
    if (!confirmed) {
      return;
    }
    setDeleting(true);
    setError("");
    setNotice("");
    try {
      const response = await deleteResearchRecords(selectedIds);
      const selected = new Set(selectedIds);
      setSelectedRecordIds({});
      if (selectedId && selected.has(selectedId)) {
        setSelectedId(null);
        setRecord(null);
        setDraftMarkdown("");
        setEditing(false);
      }
      setNotice(`Deleted ${response.deleted_count} document(s).`);
      await loadTree();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete selected documents.");
    } finally {
      setDeleting(false);
    }
  }

  async function onDeleteFolder(folder: string, count: number) {
    const confirmed = window.confirm(`Delete folder "${folder}" and its ${count} document(s)?`);
    if (!confirmed) {
      return;
    }
    setDeleting(true);
    setError("");
    setNotice("");
    try {
      const response = await deleteResearchFolder(folder);
      const deletedIds = new Set(response.deleted_ids);
      setSelectedRecordIds((previous) => {
        const next: Record<string, boolean> = {};
        for (const [id, checked] of Object.entries(previous)) {
          if (!deletedIds.has(id) && checked) {
            next[id] = true;
          }
        }
        return next;
      });
      if (selectedId && deletedIds.has(selectedId)) {
        setSelectedId(null);
        setRecord(null);
        setDraftMarkdown("");
        setEditing(false);
      }
      setNotice(`Deleted folder ${folder} (${response.deleted_count} document(s)).`);
      await loadTree();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete folder.");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <section className="panel">
      <h2>Research</h2>
      <p className="muted">Browse and edit markdown snapshots from the research store.</p>
      <div className="research-layout">
        <aside className="research-tree">
          <div className="research-tree-header">
            <h3>Documents</h3>
            <button disabled={loadingTree || deleting} onClick={() => void loadTree()} type="button">
              {loadingTree ? "Refreshing..." : "Refresh"}
            </button>
          </div>
          <div className="research-tree-actions">
            <button
              disabled={selectedIds.length === 0 || deleting}
              onClick={() => void onDeleteSelected()}
              type="button"
            >
              Delete selected ({selectedIds.length})
            </button>
          </div>
          <div className="research-tree-root">research</div>
          {!hasRecords ? <p className="muted">No research documents found.</p> : null}
          <div className="research-tree-children">
            {folders.map((folder) => {
              const isExpanded = expandedFolders[folder.folder] ?? true;
              return (
                <div className="research-folder-node" key={folder.folder}>
                  <div className="research-folder-toggle-wrap">
                    <button className="research-folder-toggle" onClick={() => toggleFolder(folder.folder)} type="button">
                      <span className="research-chevron">{isExpanded ? "▾" : "▸"}</span>
                      <strong>{folder.folder}</strong>
                      <span className="research-folder-count">{folder.records.length}</span>
                    </button>
                    <div className="research-folder-actions">
                      <button
                        disabled={deleting || folder.records.length === 0}
                        onClick={() => toggleFolderSelection(folder)}
                        type="button"
                      >
                        {folder.records.every((item) => selectedRecordIds[item.id]) ? "Unselect all" : "Select all"}
                      </button>
                      <button
                        disabled={deleting || folder.records.length === 0}
                        onClick={() => void onDeleteFolder(folder.folder, folder.records.length)}
                        type="button"
                      >
                        Delete folder
                      </button>
                    </div>
                  </div>
                  {isExpanded ? (
                    <div className="research-files">
                      {folder.records.map((item) => (
                        <div className={selectedId === item.id ? "research-file active" : "research-file"} key={item.id}>
                          <label className="research-file-select">
                            <input
                              checked={Boolean(selectedRecordIds[item.id])}
                              onChange={() => toggleRecordSelection(item.id)}
                              type="checkbox"
                            />
                            <span>Select</span>
                          </label>
                          <button className="research-file-open" onClick={() => setSelectedId(item.id)} type="button">
                            <strong>{item.file_name}</strong>
                            <span>
                              {item.ticker} / {item.timeframe}
                            </span>
                            <span>{toLocalTimestamp(item.created_at)}</span>
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        </aside>
        <article className="research-document">
          {loadingRecord ? <p className="muted">Loading document...</p> : null}
          {!loadingRecord && !record ? <p className="muted">Select a research document to view.</p> : null}
          {record ? (
            <>
              <div className="research-document-header">
                <div>
                  <h3>{record.file_name}</h3>
                  <p className="muted">
                    Updated {toLocalTimestamp(record.updated_at)} | {record.relative_path}
                  </p>
                </div>
                <div className="row-actions">
                  <button disabled={saving} onClick={() => setEditing((previous) => !previous)} type="button">
                    {editing ? "Preview" : "Edit"}
                  </button>
                  <button disabled={saving || deleting} onClick={() => void onDeleteCurrent()} type="button">
                    Delete
                  </button>
                  {editing ? (
                    <>
                      <button
                        disabled={saving}
                        onClick={() => {
                          setDraftMarkdown(record.markdown);
                          setEditing(false);
                          setNotice("");
                          setError("");
                        }}
                        type="button"
                      >
                        Cancel
                      </button>
                      <button disabled={saving} onClick={() => void onSave()} type="button">
                        {saving ? "Saving..." : "Save"}
                      </button>
                    </>
                  ) : null}
                </div>
              </div>
              {editing ? (
                <textarea
                  className="research-editor"
                  onChange={(event) => setDraftMarkdown(event.target.value)}
                  value={draftMarkdown}
                />
              ) : (
                <div className="research-preview">
                  <ReactMarkdown rehypePlugins={[rehypeSanitize]} remarkPlugins={[remarkGfm]}>
                    {record.markdown}
                  </ReactMarkdown>
                </div>
              )}
            </>
          ) : null}
          {notice ? <p className="success">{notice}</p> : null}
          {error ? <p className="error">{error}</p> : null}
        </article>
      </div>
    </section>
  );
}
