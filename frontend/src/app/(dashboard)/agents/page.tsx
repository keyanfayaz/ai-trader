"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { activateAgent, deleteAgent, listAgents, pauseAgent, streamAllAgentMonitor } from "@/lib/api";
import type { Agent, AgentFollowUpEvent, AgentThoughtSummary } from "@/lib/types";

const MAX_THOUGHT_EVENTS = 80;

type ThoughtFeedItem = {
  event: AgentFollowUpEvent["event"];
  agentId: string;
  agentName: string;
  timestamp: string;
  thoughtSummary: AgentThoughtSummary;
};

type AgentStatus = Agent["status"];

function toLocalTimestamp(value: string | null): string {
  if (!value) {
    return "N/A";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

function getRuntimeLabel(agent: Agent): string {
  if (agent.status !== "active") {
    return "Paused";
  }
  if (agent.can_run_now) {
    return "Ready now (next scheduler cycle)";
  }
  if (agent.next_run_reason === "market_closed") {
    const when = toLocalTimestamp(agent.next_run_at ?? agent.next_market_open);
    return `Market closed, next run ${when}`;
  }
  if (agent.next_run_reason === "interval_wait") {
    return `Cooldown until ${toLocalTimestamp(agent.next_run_at)}`;
  }
  return "Waiting for scheduler";
}

function parseThoughtSummary(event: AgentFollowUpEvent): AgentThoughtSummary | null {
  const raw = event.payload?.thought_summary;
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const candidate = raw as Record<string, unknown>;
  const reasons = Array.isArray(candidate.reasons) ? candidate.reasons.map((item) => String(item)) : [];
  const reasonLabels = Array.isArray(candidate.reason_labels)
    ? candidate.reason_labels.map((item) => String(item))
    : reasons;
  const actions = Array.isArray(candidate.actions) ? candidate.actions.map((item) => String(item)) : [];
  const nextSteps = Array.isArray(candidate.next_steps) ? candidate.next_steps.map((item) => String(item)) : [];
  return {
    stage: String(candidate.stage ?? "triggered"),
    reasons,
    reason_labels: reasonLabels,
    decision: String(candidate.decision ?? "followup_update"),
    actions,
    next_steps: nextSteps,
  };
}

function isFollowUpEventName(eventName: string): eventName is AgentFollowUpEvent["event"] {
  return (
    eventName === "agent_followup_triggered" ||
    eventName === "agent_strategy_refreshed" ||
    eventName === "agent_strategy_refresh_failed"
  );
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [streamError, setStreamError] = useState("");
  const [pendingAgentId, setPendingAgentId] = useState<string | null>(null);
  const [thoughtFeed, setThoughtFeed] = useState<ThoughtFeedItem[]>([]);
  const pendingStatusOverridesRef = useRef<Record<string, AgentStatus>>({});

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const response = await listAgents();
      const pendingOverrides = pendingStatusOverridesRef.current;
      setAgents(
        response.agents.map((agent) => {
          const pendingStatus = pendingOverrides[agent.agent_id];
          if (!pendingStatus) {
            return agent;
          }
          return { ...agent, status: pendingStatus };
        }),
      );
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load agents.");
    } finally {
      setLoading(false);
    }
  }

  async function toggleStatus(agent: Agent) {
    setPendingAgentId(agent.agent_id);
    const nextStatus: AgentStatus = agent.status === "active" ? "paused" : "active";
    pendingStatusOverridesRef.current = {
      ...pendingStatusOverridesRef.current,
      [agent.agent_id]: nextStatus,
    };
    setAgents((current) =>
      current.map((item) => (item.agent_id === agent.agent_id ? { ...item, status: nextStatus } : item)),
    );
    try {
      if (agent.status === "active") {
        await pauseAgent(agent.agent_id);
      } else {
        await activateAgent(agent.agent_id);
      }
      await refresh();
    } catch (toggleError) {
      pendingStatusOverridesRef.current = {
        ...pendingStatusOverridesRef.current,
        [agent.agent_id]: agent.status,
      };
      setAgents((current) =>
        current.map((item) => (item.agent_id === agent.agent_id ? { ...item, status: agent.status } : item)),
      );
      setError(toggleError instanceof Error ? toggleError.message : "Unable to update agent status.");
    } finally {
      const nextOverrides = { ...pendingStatusOverridesRef.current };
      delete nextOverrides[agent.agent_id];
      pendingStatusOverridesRef.current = nextOverrides;
      setPendingAgentId(null);
    }
  }

  async function removeAgent(agent: Agent) {
    const shouldDelete = window.confirm(
      `Delete agent "${agent.name}"? This stops any active runs and removes it permanently.`,
    );
    if (!shouldDelete) {
      return;
    }
    setPendingAgentId(agent.agent_id);
    try {
      await deleteAgent(agent.agent_id);
      setAgents((current) => current.filter((item) => item.agent_id !== agent.agent_id));
      setThoughtFeed((current) => current.filter((item) => item.agentId !== agent.agent_id));
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete agent.");
    } finally {
      setPendingAgentId(null);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    const handler = () => {
      void refresh();
    };
    window.addEventListener("agents:refresh", handler);
    return () => window.removeEventListener("agents:refresh", handler);
  }, []);

  useEffect(() => {
    const close = streamAllAgentMonitor({
      onEvent: (event) => {
        setStreamError("");
        if (event.event === "agent_status_changed") {
          void refresh();
        }
        if (event.event === "agent_run_completed") {
          const payloadExecutedAt = event.payload?.executed_at;
          const completedAt =
            typeof payloadExecutedAt === "string" && payloadExecutedAt.trim()
              ? payloadExecutedAt
              : event.timestamp;
          if (completedAt) {
            setAgents((current) =>
              current.map((agent) =>
                agent.agent_id === event.agent_id ? { ...agent, last_run_at: completedAt } : agent,
              ),
            );
          }
        }
        if (!isFollowUpEventName(event.event)) {
          return;
        }
        const followupEvent: AgentFollowUpEvent = {
          ...event,
          event: event.event,
          payload: event.payload,
        };
        const summaryPayload = parseThoughtSummary(followupEvent);
        if (!summaryPayload) {
          return;
        }
        setThoughtFeed((previous) => {
          const nameById = Object.fromEntries(agents.map((agent) => [agent.agent_id, agent.name]));
          const entry: ThoughtFeedItem = {
            event: followupEvent.event,
            agentId: followupEvent.agent_id,
            agentName: nameById[followupEvent.agent_id] ?? followupEvent.agent_id,
            timestamp: followupEvent.timestamp,
            thoughtSummary: summaryPayload,
          };
          return [entry, ...previous].slice(0, MAX_THOUGHT_EVENTS);
        });
      },
      onError: (message) => {
        setStreamError(message);
      },
    });
    return () => close();
  }, [agents]);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Agents</h2>
        <button onClick={() => void refresh()} type="button">
          Refresh
        </button>
      </div>
      {loading ? <p className="muted">Loading agents...</p> : null}
      {error ? <p className="error">{error}</p> : null}
      {!loading && agents.length === 0 ? <p className="muted">No agents created yet.</p> : null}
      {agents.length > 0 ? (
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Symbols</th>
              <th>Risk</th>
              <th>Execution</th>
              <th>Status</th>
              <th>Next Scheduled Run</th>
              <th>Last Run</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {agents.map((agent) => (
              <tr key={agent.agent_id}>
                <td>{agent.name}</td>
                <td>
                  {agent.symbols.join(", ")}
                  {agent.asset_class === "future" ? (
                    <span className="tag" style={{ marginLeft: 6 }}>
                      Futures
                    </span>
                  ) : null}
                </td>
                <td>{agent.risk_profile}</td>
                <td>Global trading mode (paper/live)</td>
                <td>{agent.mission_status ? `${agent.status} · ${agent.mission_status}` : agent.status}</td>
                <td>
                  {agent.mission_status === "executing" && agent.window_ends_at
                    ? `Window ends ${toLocalTimestamp(agent.window_ends_at)}`
                    : getRuntimeLabel(agent)}
                </td>
                <td>{agent.last_run_at ? toLocalTimestamp(agent.last_run_at) : "Never"}</td>
                <td>
                  <div className="row-actions">
                    <Link href={`/agents/${agent.agent_id}`} className="link-button">
                      Monitor
                    </Link>
                    <button
                      onClick={() => void toggleStatus(agent)}
                      type="button"
                      disabled={pendingAgentId === agent.agent_id}
                    >
                      {agent.status === "active" ? "Pause" : "Activate"}
                    </button>
                    <button
                      onClick={() => void removeAgent(agent)}
                      type="button"
                      disabled={pendingAgentId === agent.agent_id}
                    >
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      <div className="overview-section">
        <h3>Agent Follow-Up Thought Stream</h3>
        {streamError ? <p className="error">{streamError}</p> : null}
        {thoughtFeed.length === 0 ? (
          <p className="muted">No follow-up thought events yet.</p>
        ) : (
          <div className="monitor-events">
            {thoughtFeed.map((item, index) => (
              <article key={`${item.timestamp}-${item.agentId}-${item.event}-${index}`} className="monitor-event-item">
                <div className="monitor-event-meta">
                  <strong>{item.event}</strong>
                  <span>
                    {toLocalTimestamp(item.timestamp)} - {item.agentName}
                  </span>
                </div>
                <p>
                  <strong>Decision:</strong> {item.thoughtSummary.decision}
                </p>
                <p>
                  <strong>Reasons:</strong>{" "}
                  {item.thoughtSummary.reason_labels.length > 0 ? item.thoughtSummary.reason_labels.join(" | ") : "None"}
                </p>
                <p>
                  <strong>Actions:</strong>{" "}
                  {item.thoughtSummary.actions.length > 0 ? item.thoughtSummary.actions.join(", ") : "None"}
                </p>
                <p>
                  <strong>Next Steps:</strong>{" "}
                  {item.thoughtSummary.next_steps.length > 0 ? item.thoughtSummary.next_steps.join(", ") : "None"}
                </p>
              </article>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
