"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { getTradingMode, setTradingMode } from "@/lib/api";
import type { TradingMode } from "@/lib/types";

const NAV_ITEMS = [
  { href: "/new", label: "New" },
  { href: "/agents", label: "Agents" },
  { href: "/overview", label: "Overview" },
  { href: "/research", label: "Research" },
  { href: "/skills", label: "Skills" },
  { href: "/settings", label: "Settings" },
];

export function Sidebar() {
  const pathname = usePathname();
  const [tradingMode, setLocalTradingMode] = useState<TradingMode>("paper");
  const [loadingMode, setLoadingMode] = useState(true);
  const [updatingMode, setUpdatingMode] = useState(false);
  const [modeError, setModeError] = useState("");

  useEffect(() => {
    let mounted = true;
    void (async () => {
      try {
        const response = await getTradingMode();
        if (mounted) {
          setLocalTradingMode(response.trading_mode);
        }
      } catch (error) {
        if (mounted) {
          setModeError(error instanceof Error ? error.message : "Unable to load trading mode.");
        }
      } finally {
        if (mounted) {
          setLoadingMode(false);
        }
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  async function onToggleMode() {
    if (updatingMode || loadingMode) {
      return;
    }
    const nextMode: TradingMode = tradingMode === "paper" ? "real" : "paper";
    const confirmed = window.confirm(
      nextMode === "real"
        ? "Switch to LIVE mode? New executions will use REAL Alpaca API keys."
        : "Switch back to PAPER mode? New executions will use PAPER Alpaca API keys.",
    );
    if (!confirmed) {
      return;
    }
    setUpdatingMode(true);
    setModeError("");
    try {
      const response = await setTradingMode(nextMode);
      setLocalTradingMode(response.trading_mode);
    } catch (error) {
      setModeError(error instanceof Error ? error.message : "Unable to update trading mode.");
    } finally {
      setUpdatingMode(false);
    }
  }

  return (
    <aside className="dashboard-sidebar">
      <div>
        <h1>Auto Trader</h1>
        <p>{tradingMode === "paper" ? "Paper mode enabled" : "LIVE mode enabled"}</p>
      </div>
      <nav>
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Link
              className={isActive ? "nav-link active" : "nav-link"}
              href={item.href}
              key={item.href}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="mode-toggle-block">
        <label htmlFor="trading-mode-toggle">
          <span>Trading Mode</span>
        </label>
        <button
          id="trading-mode-toggle"
          disabled={loadingMode || updatingMode}
          onClick={() => void onToggleMode()}
          type="button"
        >
          {loadingMode ? "Loading..." : tradingMode === "paper" ? "Switch to LIVE" : "Switch to PAPER"}
        </button>
        {modeError ? <p className="error">{modeError}</p> : null}
      </div>
    </aside>
  );
}
