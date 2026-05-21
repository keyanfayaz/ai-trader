"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  activateAgent,
  getAgentMonitor,
  listSkills,
  pauseAgent,
  setAgentSkills,
  streamAgentMonitor,
} from "@/lib/api";
import type {
  AgentFollowUpEvent,
  AgentMonitorEvent,
  AgentMonitorResponse,
  AgentThoughtSummary,
  SkillSummary,
} from "@/lib/types";

const MAX_EVENT_FEED = 100;
const MAX_THOUGHT_EVENTS = 40;

type ThoughtFeedItem = {
  event: AgentFollowUpEvent["event"];
  timestamp: string;
  thoughtSummary: AgentThoughtSummary;
};

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

function getRuntimeLabel(data: AgentMonitorResponse | null): string {
  if (!data) {
    return "N/A";
  }
  const agent = data.agent;
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

function isFollowUpEventName(eventName: string): eventName is AgentFollowUpEvent["event"] {
  return (
    eventName === "agent_followup_triggered" ||
    eventName === "agent_strategy_refreshed" ||
    eventName === "agent_strategy_refresh_failed"
  );
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

export default function AgentMonitorPage() {
  const params = useParams<{ agentId: string }>();
  const agentId = params.agentId;

  const [data, setData] = useState<AgentMonitorResponse | null>(null);
  const [events, setEvents] = useState<AgentMonitorEvent[]>([]);
  const [thoughtFeed, setThoughtFeed] = useState<ThoughtFeedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [streamError, setStreamError] = useState("");
  const [availableSkills, setAvailableSkills] = useState<SkillSummary[]>([]);
  const [skillSelection, setSkillSelection] = useState<string[]>([]);
  const [savingSkills, setSavingSkills] = useState(false);
  const [skillNotice, setSkillNotice] = useState("");
  const [nowTick, setNowTick] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowTick(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const refresh = useCallback(async () => {
    if (!agentId) {
      return;
    }
    setLoading(true);
    setError("");
    try {
      const response = await getAgentMonitor(agentId);
      setData(response);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load monitor data.");
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  async function toggleStatus() {
    if (!data) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (data.agent.status === "active") {
        await pauseAgent(data.agent.agent_id);
      } else {
        await activateAgent(data.agent.agent_id);
      }
      await refresh();
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : "Unable to update status.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    void (async () => {
      try {
        const response = await listSkills();
        setAvailableSkills(response.skills);
      } catch {
        // Skills are optional; ignore load failures on the monitor page.
      }
    })();
  }, []);

  useEffect(() => {
    if (data?.agent.skill_ids) {
      setSkillSelection(data.agent.skill_ids);
    }
  }, [data?.agent.skill_ids]);

  async function saveSkillSelection() {
    if (!data) {
      return;
    }
    setSavingSkills(true);
    setSkillNotice("");
    setError("");
    try {
      await setAgentSkills(data.agent.agent_id, skillSelection);
      setSkillNotice("Skills updated. They apply on the next strategy refresh.");
      await refresh();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to update skills.");
    } finally {
      setSavingSkills(false);
    }
  }

  useEffect(() => {
    if (!agentId) {
      return;
    }
    const close = streamAgentMonitor(agentId, {
      onEvent: (event) => {
        setStreamError("");
        setEvents((previous) => [event, ...previous].slice(0, MAX_EVENT_FEED));
        if (
          event.event === "agent_run_completed" ||
          event.event === "agent_status_changed" ||
          event.event.startsWith("mission_")
        ) {
          void refresh();
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
          const entry: ThoughtFeedItem = {
            event: followupEvent.event,
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
  }, [agentId, refresh]);

  const latestRun = data?.latest_run;
  const statusLabel = data?.agent.status ?? "paused";
  const statusClass = statusLabel === "active" ? "success" : "muted";
  const monitorTitle = useMemo(() => data?.agent.name ?? "Agent Monitor", [data?.agent.name]);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>{monitorTitle}</h2>
        <div className="monitor-actions">
          <Link href="/agents" className="link-button">
            Back to Agents
          </Link>
          <button onClick={() => void refresh()} type="button" disabled={loading || busy}>
            Refresh
          </button>
          <button onClick={() => void toggleStatus()} type="button" disabled={!data || loading || busy}>
            {data?.agent.status === "active" ? "Pause" : "Activate"}
          </button>
        </div>
      </div>

      {loading ? <p className="muted">Loading monitor...</p> : null}
      {error ? <p className="error">{error}</p> : null}
      {streamError ? <p className="error">{streamError}</p> : null}

      {data ? (
        <>
          <div className="monitor-grid">
            <article>
              <h3>Status</h3>
              <p className={statusClass}>{statusLabel}</p>
            </article>
            <article>
              <h3>Symbols</h3>
              <p>{data.agent.symbols.join(", ")}</p>
            </article>
            <article>
              <h3>Timeframe</h3>
              <p>{data.agent.timeframe}</p>
            </article>
            <article>
              <h3>Last Run</h3>
              <p>{data.agent.last_run_at ? toLocalTimestamp(data.agent.last_run_at) : "Never"}</p>
            </article>
            <article>
              <h3>Next Scheduled Run</h3>
              <p>{getRuntimeLabel(data)}</p>
            </article>
          </div>

          {data.agent.mission_status ? (
            <div className="monitor-section">
              <h3>Mission</h3>
              <div className="monitor-grid">
                <article>
                  <h3>Mission Status</h3>
                  <p>{data.agent.mission_status}</p>
                </article>
                <article>
                  <h3>Window</h3>
                  <p>
                    {data.agent.strategy_window_seconds
                      ? `${Math.round(data.agent.strategy_window_seconds / 60)} min`
                      : "n/a"}
                  </p>
                </article>
                <article>
                  <h3>Time Remaining</h3>
                  <p>
                    {(() => {
                      if (!data.agent.window_ends_at) {
                        return data.agent.mission_status === "armed"
                          ? "Waiting for market open"
                          : "n/a";
                      }
                      const ms = new Date(data.agent.window_ends_at).getTime() - nowTick;
                      if (ms <= 0) return "Window ended";
                      const total = Math.floor(ms / 1000);
                      const m = Math.floor(total / 60);
                      const s = total % 60;
                      return `${m}m ${s}s`;
                    })()}
                  </p>
                </article>
                <article>
                  <h3>Terminal Reason</h3>
                  <p>{data.agent.terminal_reason ?? "—"}</p>
                </article>
              </div>
              {Array.isArray(
                (data.agent.execution_plan as { legs?: unknown[] } | undefined)?.legs,
              ) ? (
                <table className="table">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Entry</th>
                      <th>Target</th>
                      <th>Stop</th>
                      <th>Weight</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(
                      (data.agent.execution_plan as {
                        legs?: Array<{
                          symbol: string;
                          entry?: { type?: string; ref_price?: number | null };
                          target_pct?: number;
                          stop_pct?: number;
                          weight?: number;
                        }>;
                      }).legs ?? []
                    ).map((leg) => (
                      <tr key={leg.symbol}>
                        <td>{leg.symbol}</td>
                        <td>{leg.entry?.type ?? "market"}</td>
                        <td>+{leg.target_pct}%</td>
                        <td>-{leg.stop_pct}%</td>
                        <td>{Math.round((leg.weight ?? 0) * 100)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
              <h3 style={{ marginTop: 14 }}>Decision Log</h3>
              {data.agent.decision_log && data.agent.decision_log.length > 0 ? (
                <table className="table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Phase</th>
                      <th>Symbol</th>
                      <th>Action</th>
                      <th>Reason</th>
                      <th>Fill</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...data.agent.decision_log]
                      .reverse()
                      .slice(0, 100)
                      .map((entry, idx) => (
                        <tr key={`${entry.at}-${idx}`}>
                          <td>{toLocalTimestamp(entry.at)}</td>
                          <td>{entry.phase}</td>
                          <td>{entry.symbol || "—"}</td>
                          <td>{entry.action}</td>
                          <td>{entry.reason}</td>
                          <td>
                            {entry.filled_qty
                              ? `${entry.filled_qty} @ ${entry.filled_avg_price ?? "?"}`
                              : "—"}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              ) : (
                <p className="muted">No decisions logged yet.</p>
              )}
            </div>
          ) : null}

          <div className="monitor-section">
            <h3>Skills</h3>
            <p className="muted">
              Bound skills are injected as instructional context into this agent&apos;s strategy
              generation and runtime strategy refresh.
            </p>
            {skillNotice ? <p className="success">{skillNotice}</p> : null}
            {availableSkills.length === 0 ? (
              <p className="muted">
                No skills in the library yet. Create one from the Skills tab.
              </p>
            ) : (
              <>
                <div className="form-grid" style={{ maxWidth: "none" }}>
                  {availableSkills.map((skill) => {
                    const checked = skillSelection.includes(skill.id);
                    return (
                      <label key={skill.id} className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={savingSkills}
                          onChange={(event) => {
                            setSkillSelection((previous) =>
                              event.target.checked
                                ? [...previous, skill.id]
                                : previous.filter((id) => id !== skill.id),
                            );
                          }}
                        />
                        <span>
                          <strong>{skill.name}</strong> — {skill.description}
                        </span>
                      </label>
                    );
                  })}
                </div>
                <div className="row-actions" style={{ marginTop: 12 }}>
                  <button
                    type="button"
                    disabled={savingSkills}
                    onClick={() => void saveSkillSelection()}
                  >
                    {savingSkills ? "Saving…" : "Save skills"}
                  </button>
                </div>
              </>
            )}
          </div>

          <div className="monitor-section">
            <h3>Run Response Thought Process</h3>
            {thoughtFeed.length === 0 ? (
              <p className="muted">No follow-up thought process events yet.</p>
            ) : (
              <div className="monitor-events">
                {thoughtFeed.map((item, index) => (
                  <article key={`${item.timestamp}-${item.event}-${index}`} className="monitor-event-item">
                    <div className="monitor-event-meta">
                      <strong>{item.event}</strong>
                      <span>{toLocalTimestamp(item.timestamp)}</span>
                    </div>
                    <p>
                      <strong>Decision:</strong> {item.thoughtSummary.decision}
                    </p>
                    <p>
                      <strong>Reasons:</strong>{" "}
                      {item.thoughtSummary.reason_labels.length > 0
                        ? item.thoughtSummary.reason_labels.join(" | ")
                        : "None"}
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

          <div className="monitor-section">
            <h3>Latest Run</h3>
            {latestRun ? (
              <div className="summary-grid">
                <article>
                  <h3>Executed</h3>
                  <p>{toLocalTimestamp(latestRun.executed_at)}</p>
                </article>
                <article>
                  <h3>Spent</h3>
                  <p>${latestRun.spent.toFixed(2)}</p>
                </article>
                <article>
                  <h3>Earned</h3>
                  <p>${latestRun.earned.toFixed(2)}</p>
                </article>
                <article>
                  <h3>Net P/L</h3>
                  <p className={latestRun.net_pnl >= 0 ? "positive" : "negative"}>${latestRun.net_pnl.toFixed(2)}</p>
                </article>
                <article>
                  <h3>Trade Decisions</h3>
                  <p>{latestRun.trades.length}</p>
                </article>
              </div>
            ) : (
              <p className="muted">No runs yet.</p>
            )}
          </div>

          <div className="monitor-section">
            <h3>Latest Decision Log (Executed + Non-Executed)</h3>
            <p className="muted">
              BUY/SELL decisions can submit broker orders. HOLD and blocked decisions are logged without order
              submission.
            </p>
            {latestRun && latestRun.trades.length > 0 ? (
              <table className="table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Signal</th>
                    <th>Side</th>
                    <th>Qty</th>
                    <th>Price</th>
                    <th>Value</th>
                    <th>Status</th>
                    <th>Policy</th>
                  </tr>
                </thead>
                <tbody>
                  {latestRun.trades.map((trade, index) => (
                    <tr key={`${trade.symbol}-${index}`}>
                      <td>{trade.symbol}</td>
                      <td>{trade.signal}</td>
                      <td>{trade.side}</td>
                      <td>{trade.qty.toFixed(4)}</td>
                      <td>${trade.latest_close.toFixed(2)}</td>
                      <td>${trade.value.toFixed(2)}</td>
                      <td>{trade.order_status}</td>
                      <td>{trade.policy_action ?? "n/a"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted">No decision entries yet.</p>
            )}
          </div>

          <div className="monitor-section">
            <h3>Recent Runs</h3>
            {data.recent_runs.length > 0 ? (
              <table className="table">
                <thead>
                  <tr>
                    <th>Executed</th>
                    <th>Spent</th>
                    <th>Earned</th>
                    <th>Net P/L</th>
                    <th>Trades</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_runs.map((run) => (
                    <tr key={run.executed_at}>
                      <td>{toLocalTimestamp(run.executed_at)}</td>
                      <td>${run.spent.toFixed(2)}</td>
                      <td>${run.earned.toFixed(2)}</td>
                      <td className={run.net_pnl >= 0 ? "positive" : "negative"}>${run.net_pnl.toFixed(2)}</td>
                      <td>{run.trades.length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted">No run history yet.</p>
            )}
          </div>

          <div className="monitor-section">
            <h3>Live Event Feed</h3>
            {events.length === 0 ? (
              <p className="muted">No live events yet.</p>
            ) : (
              <div className="monitor-events">
                {events.map((event, index) => (
                  <article key={`${event.timestamp}-${event.event}-${index}`} className="monitor-event-item">
                    <div className="monitor-event-meta">
                      <strong>{event.event}</strong>
                      <span>{toLocalTimestamp(event.timestamp)}</span>
                    </div>
                    <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                  </article>
                ))}
              </div>
            )}
          </div>
        </>
      ) : null}
    </section>
  );
}
