"use client";

import { useEffect, useMemo, useState } from "react";

import { activateAgent, listAgents } from "@/lib/api";
import type { Agent } from "@/lib/types";

const SESSION_KEY = "startup-resume-dialog-handled-v1";

export function StartupResumeDialog() {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selection, setSelection] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");

  const selectedAgentIds = useMemo(
    () => Object.entries(selection).filter(([, isSelected]) => isSelected).map(([agentId]) => agentId),
    [selection],
  );

  useEffect(() => {
    let mounted = true;
    if (typeof window === "undefined" || window.sessionStorage.getItem(SESSION_KEY) === "1") {
      return;
    }
    void (async () => {
      try {
        const response = await listAgents();
        if (!mounted) {
          return;
        }
        const candidates = response.agents.filter((agent) => agent.startup_pause_pending);
        if (candidates.length === 0) {
          window.sessionStorage.setItem(SESSION_KEY, "1");
          return;
        }
        setAgents(candidates);
        setSelection(Object.fromEntries(candidates.map((agent) => [agent.agent_id, true])));
        setOpen(true);
      } catch {
        // Fail open: no dialog if bootstrap read fails.
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  function closeForSession() {
    if (typeof window !== "undefined") {
      window.sessionStorage.setItem(SESSION_KEY, "1");
    }
    setOpen(false);
  }

  async function onResumeSelected() {
    if (selectedAgentIds.length === 0) {
      setError("Select at least one agent to resume.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      await Promise.allSettled(selectedAgentIds.map((agentId) => activateAgent(agentId)));
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("agents:refresh"));
      }
      closeForSession();
    } catch (resumeError) {
      setError(resumeError instanceof Error ? resumeError.message : "Unable to resume selected agents.");
    } finally {
      setLoading(false);
    }
  }

  if (!open) {
    return null;
  }

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(2, 6, 23, 0.78)",
        backdropFilter: "blur(2px)",
        display: "grid",
        placeItems: "center",
        zIndex: 1000,
        padding: "1rem",
      }}
    >
      <div
        style={{
          width: "min(560px, 100%)",
          borderRadius: "14px",
          padding: "1.25rem",
          background: "#0b1220",
          border: "1px solid #334155",
          color: "#f8fafc",
          boxShadow: "0 24px 48px rgba(2, 6, 23, 0.55)",
        }}
      >
        <h3 style={{ marginTop: 0, marginBottom: "0.75rem", color: "#f8fafc" }}>Resume Scheduled Executions?</h3>
        <p style={{ margin: 0, color: "#cbd5e1", lineHeight: 1.5 }}>
          These agents were active before backend restart and were auto-paused for safety. Select which agents to
          resume now.
        </p>
        <div style={{ display: "grid", gap: "0.625rem", marginTop: "0.9rem" }}>
          {agents.map((agent) => (
            <label
              key={agent.agent_id}
              style={{
                display: "flex",
                gap: "0.5rem",
                alignItems: "center",
                color: "#e2e8f0",
                fontWeight: 500,
              }}
            >
              <input
                type="checkbox"
                checked={Boolean(selection[agent.agent_id])}
                onChange={(event) => {
                  const checked = event.target.checked;
                  setSelection((current) => ({ ...current, [agent.agent_id]: checked }));
                }}
                disabled={loading}
                style={{ accentColor: "#38bdf8", width: "1rem", height: "1rem" }}
              />
              <span>
                {agent.name} ({agent.symbols.join(", ")})
              </span>
            </label>
          ))}
        </div>
        {error ? <p className="error">{error}</p> : null}
        <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end", marginTop: "1rem" }}>
          <button
            type="button"
            onClick={closeForSession}
            disabled={loading}
            style={{
              background: "transparent",
              color: "#f8fafc",
              border: "1px solid #94a3b8",
            }}
          >
            Not now
          </button>
          <button
            type="button"
            onClick={() => void onResumeSelected()}
            disabled={loading}
            style={{
              background: "#0ea5e9",
              color: "#082f49",
              border: "1px solid #38bdf8",
              fontWeight: 700,
            }}
          >
            {loading ? "Resuming..." : "Resume Selected"}
          </button>
        </div>
      </div>
    </div>
  );
}
