"use client";

import { useEffect, useMemo, useState } from "react";

import { createAgent, streamAgentMonitor, streamGenerateAgentStrategy } from "@/lib/api";
import type { Agent, AgentMonitorEvent, AgentThoughtSummary, GeneratedAgentDraft } from "@/lib/types";

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

function runtimeMessage(agent: Agent): string {
  if (agent.status !== "active") {
    return "This agent starts running once you activate it from Agents.";
  }
  if (agent.can_run_now) {
    return "Agent is eligible to run now and will execute on the next scheduler cycle.";
  }
  if (agent.next_run_reason === "market_closed") {
    const when = toLocalTimestamp(agent.next_run_at ?? agent.next_market_open);
    return `Market is closed for live trading. Next eligible run is ${when}.`;
  }
  if (agent.next_run_reason === "interval_wait") {
    return `Waiting for interval cooldown. Next run is ${toLocalTimestamp(agent.next_run_at)}.`;
  }
  return "Run timing will update after activation.";
}

type ThoughtFeedItem = {
  event: string;
  timestamp: string;
  thoughtSummary: AgentThoughtSummary;
};

type ResearchThoughtItem = {
  stage: string;
  message: string;
  source: string;
};

function toThoughtSummary(event: AgentMonitorEvent): AgentThoughtSummary | null {
  if (
    ![
      "agent_followup_triggered",
      "agent_strategy_refreshed",
      "agent_strategy_refresh_failed",
    ].includes(event.event)
  ) {
    return null;
  }
  const raw = event.payload?.thought_summary;
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const payload = raw as Record<string, unknown>;
  return {
    stage: String(payload.stage ?? "triggered"),
    reasons: Array.isArray(payload.reasons) ? payload.reasons.map((item) => String(item)) : [],
    reason_labels: Array.isArray(payload.reason_labels)
      ? payload.reason_labels.map((item) => String(item))
      : [],
    decision: String(payload.decision ?? "followup_update"),
    actions: Array.isArray(payload.actions) ? payload.actions.map((item) => String(item)) : [],
    next_steps: Array.isArray(payload.next_steps) ? payload.next_steps.map((item) => String(item)) : [],
  };
}

function toResearchThoughtItems(draft: GeneratedAgentDraft | null): ResearchThoughtItem[] {
  if (!draft?.research_thoughts || !Array.isArray(draft.research_thoughts)) {
    return [];
  }
  return draft.research_thoughts
    .map((item) => {
      if (!item || typeof item !== "object") {
        return null;
      }
      const payload = item as Record<string, unknown>;
      return {
        stage: String(payload.stage ?? "research"),
        message: String(payload.message ?? ""),
        source: String(payload.source ?? "market_research_service"),
      };
    })
    .filter((item): item is ResearchThoughtItem => Boolean(item && item.message));
}

function toCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(
    Number.isFinite(value) ? value : 0,
  );
}

export default function NewAgentPage() {
  const [generating, setGenerating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [draft, setDraft] = useState<GeneratedAgentDraft | null>(null);
  const [result, setResult] = useState<string>("");
  const [createdAgent, setCreatedAgent] = useState<Agent | null>(null);
  const [error, setError] = useState<string>("");
  const [streamError, setStreamError] = useState<string>("");
  const [thoughtFeed, setThoughtFeed] = useState<ThoughtFeedItem[]>([]);
  const [researchStreamThoughts, setResearchStreamThoughts] = useState<ResearchThoughtItem[]>([]);
  const [researchThoughtPaneOpen, setResearchThoughtPaneOpen] = useState(true);

  const selectedTicker = useMemo(() => draft?.ticker ?? "", [draft]);
  const researchThoughts = useMemo(() => toResearchThoughtItems(draft), [draft]);
  const evaluation = draft?.strategy_evaluation;
  const researchRefs = draft?.research_refs ?? [];
  const qualityFindings = draft?.quality_findings ?? null;
  const isFindingsFallback = qualityFindings?.mode === "strict_quality_fallback";
  const confidencePercent = useMemo(() => {
    if (!evaluation) {
      return 0;
    }
    return Math.round(evaluation.confidence * 100);
  }, [evaluation]);
  const isRecommended = evaluation?.recommendation === "recommended";

  async function onGenerate() {
    setGenerating(true);
    setError("");
    setResult("");
    setCreatedAgent(null);
    setResearchStreamThoughts([]);
    setResearchThoughtPaneOpen(true);
    await new Promise<void>((resolve) => {
      const close = streamGenerateAgentStrategy({
        onThought: (item) => {
          const payload = item as Record<string, unknown>;
          const thought: ResearchThoughtItem = {
            stage: String(payload.stage ?? "research"),
            message: String(payload.message ?? ""),
            source: String(payload.source ?? "market_research_service"),
          };
          if (!thought.message) {
            return;
          }
          setResearchStreamThoughts((previous) => [...previous, thought].slice(-120));
        },
        onCompleted: (response) => {
          setDraft(response.generated);
          setGenerating(false);
          setResearchThoughtPaneOpen(false);
          close();
          resolve();
        },
        onFailed: (message) => {
          setError(message);
          setDraft(null);
          setGenerating(false);
          close();
          resolve();
        },
        onError: (message) => {
          setError(message);
        },
      });
    });
  }

  async function onCreateAgent() {
    if (!draft) {
      return;
    }
    setSubmitting(true);
    setError("");
    setStreamError("");
    setResult("");
    setCreatedAgent(null);
    setThoughtFeed([]);
    try {
      const response = await createAgent({ generated: draft });
      setResult(`Created ${response.agent_id} with ${response.status} status.`);
      setCreatedAgent(response);
    } catch (createError) {
      const rawMessage = createError instanceof Error ? createError.message : "Unable to create agent.";
      if (rawMessage.includes("409")) {
        setError("A similar strategy already exists or was researched recently. Generate a new strategy and try again.");
      } else {
        setError(rawMessage);
      }
    } finally {
      setSubmitting(false);
    }
  }

  useEffect(() => {
    if (!createdAgent) {
      return;
    }
    const close = streamAgentMonitor(createdAgent.agent_id, {
      onEvent: (event) => {
        const summary = toThoughtSummary(event);
        if (!summary) {
          return;
        }
        setStreamError("");
        setThoughtFeed((previous) =>
          [
            {
              event: event.event,
              timestamp: event.timestamp,
              thoughtSummary: summary,
            },
            ...previous,
          ].slice(0, 40),
        );
      },
      onError: (message) => {
        setStreamError(message);
      },
    });
    return () => close();
  }, [createdAgent]);

  return (
    <section className="panel">
      <h2>New Agent</h2>
      <p className="muted">
        Generate a ticker and AI strategy from market research + signal services, then create the
        agent from that generated draft.
      </p>
      <div className="form-grid">
        <button disabled={generating || submitting} onClick={() => void onGenerate()} type="button">
          {generating ? "Generating..." : "Generate Strategy"}
        </button>
        <button disabled={!draft || generating || submitting} onClick={() => void onCreateAgent()} type="button">
          {submitting ? "Creating..." : "Create Agent"}
        </button>
      </div>
      {generating || draft ? (
        <div style={{ marginTop: 12 }}>
          <div className="panel-header" style={{ padding: 0 }}>
            <h3>Research Thought Process</h3>
            <button onClick={() => setResearchThoughtPaneOpen((value) => !value)} type="button">
              {researchThoughtPaneOpen ? "Collapse" : "Expand"}
            </button>
          </div>
          {researchThoughtPaneOpen ? (
            <>
              {!generating && qualityFindings ? (
                <p className="muted">
                  Quality findings available for this run.{" "}
                  <a href="#quality-findings">Jump to findings table</a>.
                </p>
              ) : null}
              {(generating ? researchStreamThoughts : researchThoughts).length === 0 ? (
                <p className="muted">
                  {generating
                    ? "Waiting for research thoughts..."
                    : "No structured research thoughts were returned for this run."}
                </p>
              ) : (
                <div className="monitor-events">
                  {(generating ? researchStreamThoughts : researchThoughts).map((item, index) => (
                    <article key={`${item.stage}-${index}`} className="monitor-event-item">
                      <div className="monitor-event-meta">
                        <strong>{item.stage}</strong>
                        <span>{item.source}</span>
                      </div>
                      <p>{item.message}</p>
                    </article>
                  ))}
                </div>
              )}
            </>
          ) : (
            <p className="muted">
              Thought process collapsed. Expand to review research and signal evaluation steps.
            </p>
          )}
        </div>
      ) : null}
      {draft ? (
        <div style={{ marginTop: 16 }}>
          {qualityFindings ? (
            <div id="quality-findings" style={{ marginBottom: 12 }}>
              <h3>Quality Findings</h3>
              <p className="muted">
                Mode: <strong>{qualityFindings.mode}</strong> | Passed {qualityFindings.quality_universe_count} of{" "}
                {qualityFindings.scan_count} scanned symbols.
              </p>
              <p className="muted">
                Filters: min price {toCurrency(qualityFindings.thresholds.min_price)}, min dollar volume{" "}
                {toCurrency(qualityFindings.thresholds.min_dollar_volume)}, min avg volume{" "}
                {Math.round(qualityFindings.thresholds.min_average_volume).toLocaleString()}, volume ratio{" "}
                {qualityFindings.thresholds.min_volume_ratio.toFixed(2)} -{" "}
                {qualityFindings.thresholds.max_volume_ratio.toFixed(2)}.
              </p>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" }}>
                  <thead>
                    <tr>
                      <th style={{ textAlign: "left", padding: "6px 8px" }}>Symbol</th>
                      <th style={{ textAlign: "left", padding: "6px 8px" }}>Signal</th>
                      <th style={{ textAlign: "left", padding: "6px 8px" }}>Price</th>
                      <th style={{ textAlign: "left", padding: "6px 8px" }}>Dollar Vol</th>
                      <th style={{ textAlign: "left", padding: "6px 8px" }}>Vol Ratio</th>
                      <th style={{ textAlign: "left", padding: "6px 8px" }}>Predictability</th>
                      <th style={{ textAlign: "left", padding: "6px 8px" }}>Checks</th>
                    </tr>
                  </thead>
                  <tbody>
                    {qualityFindings.rows.map((row) => (
                      <tr key={row.symbol}>
                        <td style={{ padding: "6px 8px" }}>{row.symbol}</td>
                        <td style={{ padding: "6px 8px" }}>{row.signal}</td>
                        <td style={{ padding: "6px 8px" }}>{toCurrency(row.latest_close)}</td>
                        <td style={{ padding: "6px 8px" }}>{toCurrency(row.dollar_volume_estimate)}</td>
                        <td style={{ padding: "6px 8px" }}>{row.volume_ratio.toFixed(2)}</td>
                        <td style={{ padding: "6px 8px" }}>{Math.round(row.predictability_score * 100)}%</td>
                        <td style={{ padding: "6px 8px" }}>
                          {row.passed_quality_filters
                            ? "passed"
                            : row.failed_checks.map((value) => value.replaceAll("_", " ")).join(", ")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
          <p>
            <strong>Ticker:</strong> {selectedTicker}
          </p>
          <p>
            <strong>Generated Name:</strong> {draft.name}
          </p>
          <p>
            <strong>Timeframe:</strong> {draft.timeframe} | <strong>Risk:</strong> {draft.risk_profile} |{" "}
            <strong>Interval:</strong> {draft.interval_seconds}s
          </p>
          <p className="muted">
            Based on {researchRefs.length} recent research snapshot{researchRefs.length === 1 ? "" : "s"}.
          </p>
          <p className="muted">
            New agents are created paused. Once activated, runs execute during market-open windows using the current
            global trading mode (paper or live).
          </p>
          {evaluation ? (
            <>
              <p className={isRecommended ? "success" : "error"}>
                <strong>{isRecommended ? "Recommended" : "Not Recommended"}</strong> - Confidence{" "}
                {confidencePercent}% (threshold {Math.round(evaluation.threshold * 100)}%)
              </p>
              {isFindingsFallback ? (
                <p className="error">
                  Strict quality filters produced no qualifying symbols for this run. A fallback candidate was used;
                  review findings before creating an agent.
                </p>
              ) : null}
              {!isRecommended ? (
                <p className="muted">
                  Confidence is below 70%. You can still create this agent, but treat it as a lower-conviction setup.
                </p>
              ) : null}
              <p>{evaluation.decision_summary}</p>
              <h3>Signals and Rationale</h3>
              <ul>
                {evaluation.key_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
              <h3>High-Level Plan</h3>
              <ul>
                {evaluation.high_level_plan.map((planItem) => (
                  <li key={planItem}>{planItem}</li>
                ))}
              </ul>
              <h3>Risk Notes</h3>
              <ul>
                {evaluation.risk_notes.length > 0 ? (
                  evaluation.risk_notes.map((riskNote) => <li key={riskNote}>{riskNote}</li>)
                ) : (
                  <li>No additional risk notes provided.</li>
                )}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
      {result ? <p className="success">{result}</p> : null}
      {createdAgent ? (
        <div style={{ marginTop: 12 }}>
          <p>
            <strong>Run Timing:</strong> {runtimeMessage(createdAgent)}
          </p>
          {createdAgent.next_market_open ? (
            <p className="muted">
              <strong>Next Market Open:</strong> {toLocalTimestamp(createdAgent.next_market_open)}
            </p>
          ) : null}
          <div style={{ marginTop: 12 }}>
            <h3>Follow-Up Thought Stream</h3>
            {streamError ? <p className="error">{streamError}</p> : null}
            {thoughtFeed.length === 0 ? (
              <p className="muted">No follow-up thought updates yet for this agent.</p>
            ) : (
              <div className="monitor-events">
                {thoughtFeed.map((item, index) => (
                  <article key={`${item.timestamp}-${item.event}-${index}`} className="monitor-event-item">
                    <div className="monitor-event-meta">
                      <strong>{item.event}</strong>
                      <span>{new Date(item.timestamp).toLocaleString()}</span>
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
                      {item.thoughtSummary.actions.length > 0
                        ? item.thoughtSummary.actions.join(", ")
                        : "None"}
                    </p>
                    <p>
                      <strong>Next Steps:</strong>{" "}
                      {item.thoughtSummary.next_steps.length > 0
                        ? item.thoughtSummary.next_steps.join(", ")
                        : "None"}
                    </p>
                  </article>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : null}
      {!createdAgent ? (
        <p className="muted" style={{ marginTop: 12 }}>
          Create an agent to watch follow-up reasoning updates.
        </p>
      ) : null}
      {error ? <p className="error">{error}</p> : null}
    </section>
  );
}
