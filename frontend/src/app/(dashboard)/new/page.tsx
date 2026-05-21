"use client";

import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { approveMission, createMission, rejectMission, streamGenerateMission } from "@/lib/api";
import type { Agent, MissionDraft } from "@/lib/types";

type ResearchThoughtItem = { stage: string; message: string; source: string };

function formatWindow(seconds: number): string {
  const mins = Math.round(seconds / 60);
  if (mins < 60) return `${mins} min`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem ? `${hrs}h ${rem}m` : `${hrs}h`;
}

export default function NewAgentPage() {
  const [inputKind, setInputKind] = useState<"ticker" | "theme">("theme");
  const [inputValue, setInputValue] = useState("");
  const [assetClass, setAssetClass] = useState<"stock" | "future">("stock");
  const [risk, setRisk] = useState("balanced");
  const [generating, setGenerating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [draft, setDraft] = useState<MissionDraft | null>(null);
  const [agent, setAgent] = useState<Agent | null>(null);
  const [windowSeconds, setWindowSeconds] = useState<number>(1800);
  const [thoughts, setThoughts] = useState<ResearchThoughtItem[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const evaluation = draft?.strategy_evaluation;
  const legs = draft?.execution_plan?.legs ?? [];
  const windowMinutes = useMemo(() => Math.round(windowSeconds / 60), [windowSeconds]);

  function onGenerate() {
    if (!inputValue.trim()) {
      setError(`Enter a ${inputKind === "theme" ? "theme" : "ticker"} first.`);
      return;
    }
    setGenerating(true);
    setError("");
    setNotice("");
    setDraft(null);
    setAgent(null);
    setThoughts([]);
    const missionInput =
      inputKind === "theme"
        ? { theme: inputValue.trim(), riskProfile: risk, assetClass }
        : { symbols: [inputValue.trim().toUpperCase()], riskProfile: risk, assetClass };
    streamGenerateMission(missionInput, {
      onThought: (item) => {
        const p = item as Record<string, unknown>;
        const message = String(p.message ?? "");
        if (!message) return;
        setThoughts((prev) => [
          ...prev,
          { stage: String(p.stage ?? "research"), message, source: String(p.source ?? "agent") },
        ].slice(-120));
      },
      onCompleted: (completed) => {
        setDraft(completed);
        setWindowSeconds(completed.window_seconds);
        setGenerating(false);
      },
      onFailed: (message) => {
        setError(message);
        setGenerating(false);
      },
      onError: (message) => setError(message),
    });
  }

  async function onCreate() {
    if (!draft) return;
    setSubmitting(true);
    setError("");
    try {
      const created = await createMission(draft, draft.preset_skill_ids);
      setAgent(created);
      setNotice("Mission created and awaiting your approval.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to create mission.");
    } finally {
      setSubmitting(false);
    }
  }

  async function onApprove() {
    if (!agent) return;
    setSubmitting(true);
    setError("");
    try {
      const updated = await approveMission(agent.agent_id, windowSeconds);
      setAgent(updated);
      setNotice(
        `Approved. The agent will execute for ${formatWindow(
          updated.strategy_window_seconds ?? windowSeconds,
        )} once the market is open, then flatten and stop.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to approve mission.");
    } finally {
      setSubmitting(false);
    }
  }

  async function onReject() {
    if (!agent) return;
    setSubmitting(true);
    setError("");
    try {
      const updated = await rejectMission(agent.agent_id);
      setAgent(updated);
      setNotice("Mission rejected.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to reject mission.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="panel">
      <h2>New Mission</h2>
      <p className="muted">
        Give a ticker or a theme. The agent researches the market, designs a single
        time-boxed strategy (it chooses the holding window and always sets an exit), and
        waits for your approval before trading. At the window&apos;s end it flattens
        everything and stops.
      </p>

      <div className="form-grid" style={{ maxWidth: 640 }}>
        <div className="row-actions">
          <button
            type="button"
            className={inputKind === "theme" ? "" : "link-button"}
            onClick={() => setInputKind("theme")}
          >
            Theme
          </button>
          <button
            type="button"
            className={inputKind === "ticker" ? "" : "link-button"}
            onClick={() => setInputKind("ticker")}
          >
            Ticker
          </button>
        </div>
        <label>
          <span>{inputKind === "theme" ? "Theme / sentiment" : "Ticker symbol"}</span>
          <input
            value={inputValue}
            placeholder={inputKind === "theme" ? "e.g. AI is hot" : "e.g. NVDA"}
            disabled={generating || submitting}
            onChange={(e) => setInputValue(e.target.value)}
          />
        </label>
        <label>
          <span>Instrument</span>
          <select
            value={assetClass}
            disabled={generating || submitting}
            onChange={(e) => setAssetClass(e.target.value as "stock" | "future")}
          >
            <option value="stock">Stocks</option>
            <option value="future">Futures</option>
          </select>
        </label>
        <label>
          <span>Risk profile</span>
          <select value={risk} disabled={generating || submitting} onChange={(e) => setRisk(e.target.value)}>
            <option value="conservative">conservative</option>
            <option value="balanced">balanced</option>
            <option value="aggressive">aggressive</option>
          </select>
        </label>
        <button type="button" disabled={generating || submitting} onClick={onGenerate}>
          {generating ? "Researching..." : "Research & develop strategy"}
        </button>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {notice ? <p className="success">{notice}</p> : null}

      {(generating || thoughts.length > 0) && !draft ? (
        <div className="monitor-section">
          <h3>Research Thought Process</h3>
          {thoughts.length === 0 ? (
            <p className="muted">Waiting for research thoughts...</p>
          ) : (
            <div className="monitor-events">
              {thoughts.map((t, i) => (
                <article key={`${t.stage}-${i}`} className="monitor-event-item">
                  <div className="monitor-event-meta">
                    <strong>{t.stage}</strong>
                    <span>{t.source}</span>
                  </div>
                  <p>{t.message}</p>
                </article>
              ))}
            </div>
          )}
        </div>
      ) : null}

      {draft ? (
        <div style={{ marginTop: 16 }}>
          <h3>{draft.name}</h3>
          {draft.input_theme ? (
            <p className="muted">
              Theme: <strong>{draft.input_theme}</strong> — {draft.theme_summary}
            </p>
          ) : null}

          <h3>Basket</h3>
          <table className="table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Score</th>
                <th>Rationale</th>
              </tr>
            </thead>
            <tbody>
              {draft.basket.map((b) => (
                <tr key={b.symbol}>
                  <td>{b.symbol}</td>
                  <td>{Math.round((b.score ?? 0) * 100)}%</td>
                  <td>{b.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>Execution Plan</h3>
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
              {legs.map((leg) => (
                <tr key={leg.symbol}>
                  <td>{leg.symbol}</td>
                  <td>
                    {leg.entry.type}
                    {leg.entry.ref_price ? ` @ ${leg.entry.ref_price}` : ""}
                  </td>
                  <td>+{leg.target_pct}%</td>
                  <td>-{leg.stop_pct}%</td>
                  <td>{Math.round((leg.weight ?? 0) * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="form-grid" style={{ maxWidth: 480 }}>
            <label>
              <span>
                Holding window (minutes) — agent recommends {windowMinutes}m.{" "}
                {draft.window_rationale}
              </span>
              <input
                type="number"
                min={5}
                max={240}
                value={windowMinutes}
                disabled={!!agent}
                onChange={(e) => setWindowSeconds(Math.max(60, Number(e.target.value) * 60))}
              />
            </label>
          </div>

          {evaluation ? (
            <>
              <p className={evaluation.recommendation === "recommended" ? "success" : "error"}>
                <strong>
                  {evaluation.recommendation === "recommended" ? "Recommended" : "Not Recommended"}
                </strong>{" "}
                — Confidence {Math.round(evaluation.confidence * 100)}%
              </p>
              <p>{evaluation.decision_summary}</p>
            </>
          ) : null}

          <h3>Generated Strategy Skill</h3>
          <p className="muted">
            Saved to your Skills library and bound to this agent so it guides every checkpoint.
          </p>
          <div className="research-preview">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
              {draft.skill_markdown}
            </ReactMarkdown>
          </div>

          {!agent ? (
            <div className="row-actions" style={{ marginTop: 14 }}>
              <button type="button" disabled={submitting} onClick={onCreate}>
                {submitting ? "Creating..." : "Create mission"}
              </button>
            </div>
          ) : agent.mission_status === "awaiting_approval" ? (
            <div className="row-actions" style={{ marginTop: 14 }}>
              <button type="button" disabled={submitting} onClick={onApprove}>
                {submitting ? "Approving..." : `Approve & arm (${windowMinutes}m)`}
              </button>
              <button type="button" className="link-button" disabled={submitting} onClick={onReject}>
                Reject
              </button>
            </div>
          ) : (
            <p className="muted" style={{ marginTop: 14 }}>
              Mission status: <strong>{agent.mission_status}</strong>. Track it on the agent
              monitor page.
            </p>
          )}
        </div>
      ) : null}
    </section>
  );
}
