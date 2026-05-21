"use client";

import { useEffect, useState } from "react";

import {
  getProviders,
  getTradingMode,
  setProviderSelection,
  setTradingMode,
} from "@/lib/api";
import type { ProviderSelection, TradingMode } from "@/lib/types";

export default function SettingsPage() {
  const [selection, setSelection] = useState<ProviderSelection | null>(null);
  const [tradingMode, setLocalTradingMode] = useState<TradingMode>("paper");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let mounted = true;
    void (async () => {
      try {
        const [providers, mode] = await Promise.all([getProviders(), getTradingMode()]);
        if (mounted) {
          setSelection(providers);
          setLocalTradingMode(mode.trading_mode);
        }
      } catch (err) {
        if (mounted) setError(err instanceof Error ? err.message : "Failed to load settings.");
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  async function applySelection(next: { data_provider?: string; broker_provider?: string }) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await setProviderSelection(next);
      setSelection(updated);
      setNotice("Provider selection updated.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to update providers.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleMode() {
    const nextMode: TradingMode = tradingMode === "paper" ? "real" : "paper";
    if (
      !window.confirm(
        nextMode === "real"
          ? "Switch to LIVE mode? New executions will use REAL broker credentials."
          : "Switch back to PAPER mode?",
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const res = await setTradingMode(nextMode);
      setLocalTradingMode(res.trading_mode);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to update trading mode.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <section className="panel">
        <h2>Settings</h2>
        <p className="muted">Loading providers...</p>
      </section>
    );
  }

  const providers = selection?.providers ?? [];
  const dataProviders = providers.filter((p) => p.market_data);
  const brokerProviders = providers.filter((p) => p.trading);
  const activeBroker = providers.find((p) => p.id === selection?.broker_provider);

  return (
    <section className="panel">
      <h2>Providers &amp; Trading</h2>
      <p className="muted">
        Choose a market-data provider and a trade-execution broker independently. View-only
        providers cannot place trades; selecting one disables order execution.
      </p>

      {error ? <p className="error">{error}</p> : null}
      {notice ? <p className="success">{notice}</p> : null}

      <div className="form-grid" style={{ maxWidth: 520 }}>
        <label>
          <span>Market data provider</span>
          <select
            value={selection?.data_provider}
            disabled={busy}
            onChange={(e) => void applySelection({ data_provider: e.target.value })}
          >
            {dataProviders.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label} {p.trading ? "" : "(data only)"}
              </option>
            ))}
          </select>
        </label>

        <label>
          <span>Broker (trade execution)</span>
          <select
            value={selection?.broker_provider}
            disabled={busy}
            onChange={(e) => void applySelection({ broker_provider: e.target.value })}
          >
            {brokerProviders.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label} ({p.asset_classes.join(", ")})
              </option>
            ))}
          </select>
        </label>

        {activeBroker?.supports_paper ? (
          <div className="mode-toggle-block">
            <span>Trading Mode: {tradingMode === "paper" ? "Paper" : "LIVE"}</span>
            <button type="button" disabled={busy} onClick={() => void toggleMode()}>
              {tradingMode === "paper" ? "Switch to LIVE" : "Switch to PAPER"}
            </button>
          </div>
        ) : (
          <p className="muted">
            The selected broker has no paper mode (real-only).
          </p>
        )}
      </div>

      <h3 style={{ marginTop: 24 }}>Provider capabilities</h3>
      <table className="table">
        <thead>
          <tr>
            <th>Provider</th>
            <th>Data</th>
            <th>Trades</th>
            <th>Asset classes</th>
            <th>Paper</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {providers.map((p) => (
            <tr key={p.id}>
              <td>
                <strong>{p.label}</strong>
              </td>
              <td>{p.market_data ? "Yes" : "No"}</td>
              <td>{p.trading ? "Yes" : "View only"}</td>
              <td>{p.asset_classes.join(", ")}</td>
              <td>{p.supports_paper ? "Yes" : "No"}</td>
              <td className="muted">{p.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
