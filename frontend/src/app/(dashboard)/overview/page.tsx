"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { getPositions, getSummary, getTransactions, listAgents, streamFollowUpThoughts } from "@/lib/api";
import type {
  Agent,
  AgentFollowUpEvent,
  AgentThoughtSummary,
  PositionsResponse,
  SummaryResponse,
  TransactionsResponse,
} from "@/lib/types";

const MAX_THOUGHT_EVENTS = 80;

type ThoughtFeedItem = {
  event: AgentFollowUpEvent["event"];
  agentId: string;
  agentName: string;
  timestamp: string;
  thoughtSummary: AgentThoughtSummary;
};

type AgentPositionCandidate = Pick<Agent, "agent_id" | "name" | "status" | "symbols" | "ticker" | "updated_at">;

type PositionPickerState = {
  symbol: string;
  candidates: AgentPositionCandidate[];
} | null;

function normalizeSymbol(value: string): string {
  return value.trim().toUpperCase();
}

function buildSymbolAgentIndex(agents: AgentPositionCandidate[]): Record<string, AgentPositionCandidate[]> {
  const index: Record<string, AgentPositionCandidate[]> = {};
  for (const agent of agents) {
    const symbolSet = new Set([...agent.symbols, agent.ticker].map(normalizeSymbol).filter(Boolean));
    for (const symbol of symbolSet) {
      const existing = index[symbol] ?? [];
      index[symbol] = [...existing, agent];
    }
  }
  return index;
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

export default function OverviewPage() {
  const router = useRouter();
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [transactions, setTransactions] = useState<TransactionsResponse | null>(null);
  const [agentNames, setAgentNames] = useState<Record<string, string>>({});
  const [agents, setAgents] = useState<AgentPositionCandidate[]>([]);
  const [thoughtFeed, setThoughtFeed] = useState<ThoughtFeedItem[]>([]);
  const [positionPicker, setPositionPicker] = useState<PositionPickerState>(null);
  const [positionLinkError, setPositionLinkError] = useState("");
  const [streamError, setStreamError] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const symbolAgentIndex = useMemo(() => buildSymbolAgentIndex(agents), [agents]);

  const refresh = useCallback(async (forceRefresh = false) => {
    setLoading(true);
    setError("");
    setPositionLinkError("");
    try {
      const [summaryResponse, positionsResponse, transactionsResponse] = await Promise.all([
        getSummary("all", { forceRefresh }),
        getPositions({ forceRefresh }),
        getTransactions(250, { forceRefresh }),
      ]);
      setSummary(summaryResponse);
      setPositions(positionsResponse);
      setTransactions(transactionsResponse);
      const agentsResponse = await listAgents();
      const mapping = Object.fromEntries(agentsResponse.agents.map((agent) => [agent.agent_id, agent.name]));
      setAgentNames(mapping);
      setAgents(
        agentsResponse.agents.map((agent) => ({
          agent_id: agent.agent_id,
          name: agent.name,
          status: agent.status,
          symbols: agent.symbols,
          ticker: agent.ticker,
          updated_at: agent.updated_at,
        })),
      );
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load overview.");
    } finally {
      setLoading(false);
    }
  }, []);

  function closePositionPicker() {
    setPositionPicker(null);
  }

  function openAgentMonitor(agentId: string) {
    closePositionPicker();
    router.push(`/agents/${agentId}`);
  }

  function onPositionSymbolClick(rawSymbol: string) {
    setPositionLinkError("");
    const symbol = normalizeSymbol(rawSymbol);
    const candidates = symbolAgentIndex[symbol] ?? [];
    if (candidates.length === 0) {
      setPositionPicker(null);
      setPositionLinkError(`No strategy agent is configured for ${symbol}.`);
      return;
    }
    if (candidates.length === 1) {
      openAgentMonitor(candidates[0].agent_id);
      return;
    }
    const sortedCandidates = [...candidates].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
    setPositionPicker({ symbol, candidates: sortedCandidates });
  }

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const close = streamFollowUpThoughts({
      onEvent: (event) => {
        setStreamError("");
        const summaryPayload = parseThoughtSummary(event);
        if (!summaryPayload) {
          return;
        }
        setThoughtFeed((previous) => {
          const entry: ThoughtFeedItem = {
            event: event.event,
            agentId: event.agent_id,
            agentName: agentNames[event.agent_id] ?? event.agent_id,
            timestamp: event.timestamp,
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
  }, [agentNames]);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Overview</h2>
        <button onClick={() => void refresh(true)} type="button">
          Refresh
        </button>
      </div>

      {loading ? <p className="muted">Loading overview...</p> : null}
      {error ? <p className="error">{error}</p> : null}

      {summary ? (
        <div className="summary-grid">
          <article>
            <h3>Portfolio Value</h3>
            <p>${summary.portfolio_value.toFixed(2)}</p>
          </article>
          <article>
            <h3>Equity</h3>
            <p>${summary.equity.toFixed(2)}</p>
          </article>
          <article>
            <h3>Cash</h3>
            <p>${summary.cash.toFixed(2)}</p>
          </article>
          <article>
            <h3>Buying Power</h3>
            <p>${summary.buying_power.toFixed(2)}</p>
          </article>
          <article>
            <h3>Day Change</h3>
            <p className={summary.day_change >= 0 ? "positive" : "negative"}>
              ${summary.day_change.toFixed(2)} ({(summary.day_change_pct * 100).toFixed(2)}%)
            </p>
          </article>
          <article>
            <h3>Account</h3>
            <p>
              {summary.status} ({summary.account_number})
            </p>
          </article>
        </div>
      ) : null}

      <div className="overview-section">
        <h3>Strategy Follow-Up Thought Stream</h3>
        {streamError ? <p className="error">{streamError}</p> : null}
        {thoughtFeed.length === 0 ? (
          <p className="muted">No follow-up thought events yet.</p>
        ) : (
          <div className="monitor-events">
            {thoughtFeed.map((item, index) => (
              <article
                key={`${item.timestamp}-${item.agentId}-${item.event}-${index}`}
                className="monitor-event-item"
              >
                <div className="monitor-event-meta">
                  <strong>{item.event}</strong>
                  <span>
                    {new Date(item.timestamp).toLocaleString()} - {item.agentName}
                  </span>
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
                  {item.thoughtSummary.next_steps.length > 0
                    ? item.thoughtSummary.next_steps.join(", ")
                    : "None"}
                </p>
              </article>
            ))}
          </div>
        )}
      </div>

      <div className="overview-section">
        <h3>Open Positions</h3>
        {positionLinkError ? <p className="error">{positionLinkError}</p> : null}
        {!loading && positions && positions.positions.length === 0 ? (
          <p className="muted">No active positions yet.</p>
        ) : null}
        {positions && positions.positions.length > 0 ? (
          <table className="table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Qty</th>
                <th>Avg entry</th>
                <th>Current Price</th>
                <th>Market value</th>
                <th>P/L (Holding)</th>
              </tr>
            </thead>
            <tbody>
              {positions.positions.map((position) => (
                <tr key={position.symbol}>
                  <td>
                    <button
                      type="button"
                      className="table-link-button"
                      onClick={() => onPositionSymbolClick(position.symbol)}
                      aria-label={`Open strategy details for ${position.symbol}`}
                    >
                      {position.symbol}
                    </button>
                  </td>
                  <td>{position.side}</td>
                  <td>{position.qty.toFixed(4)}</td>
                  <td>${position.avg_entry_price.toFixed(2)}</td>
                  <td>${position.current_price.toFixed(2)}</td>
                  <td>${position.market_value.toFixed(2)}</td>
                  <td className={position.unrealized_pnl >= 0 ? "positive" : "negative"}>
                    ${position.unrealized_pnl.toFixed(2)} ({(position.unrealized_pnl_pct * 100).toFixed(2)}%)
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        {positionPicker ? (
          <article className="position-picker">
            <div className="panel-header">
              <h4>Select strategy for {positionPicker.symbol}</h4>
              <button type="button" onClick={closePositionPicker}>
                Close
              </button>
            </div>
            <p className="muted">
              Multiple agents track this symbol. Choose the strategy page you want to open.
            </p>
            <div className="position-picker-list">
              {positionPicker.candidates.map((candidate) => (
                <button
                  key={candidate.agent_id}
                  type="button"
                  className="position-picker-item"
                  onClick={() => openAgentMonitor(candidate.agent_id)}
                >
                  <strong>{candidate.name}</strong>
                  <span>
                    Status: {candidate.status} | Ticker: {candidate.ticker}
                  </span>
                  <span>Symbols: {candidate.symbols.join(", ")}</span>
                </button>
              ))}
            </div>
          </article>
        ) : null}
      </div>

      <div className="overview-section">
        <div className="panel-header">
          <h3>Transactions (Fills)</h3>
        </div>
        <p className="muted">Live Alpaca fill activity, newest first.</p>
        {!loading && transactions && transactions.transactions.length === 0 ? (
          <p className="muted">No fill transactions yet.</p>
        ) : null}
        {transactions && transactions.transactions.length > 0 ? (
          <table className="table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Type</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Qty</th>
                <th>Price</th>
                <th>Value</th>
                <th>Order</th>
              </tr>
            </thead>
            <tbody>
              {transactions.transactions.map((item) => (
                <tr key={`${item.activity_id}-${item.executed_at}-${item.symbol}`}>
                  <td>{new Date(item.executed_at).toLocaleString()}</td>
                  <td>{item.activity_type}</td>
                  <td>{item.symbol}</td>
                  <td>{item.side.toUpperCase()}</td>
                  <td>{item.qty.toFixed(4)}</td>
                  <td>${item.price.toFixed(2)}</td>
                  <td>${item.value.toFixed(2)}</td>
                  <td>{item.order_id ?? "n/a"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>
    </section>
  );
}
