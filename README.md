# Auto Trader

Full-stack day-trading workspace for Alpaca paper and live trading:

- FastAPI backend for research, signal execution, and agent lifecycle management.
- Next.js dashboard in `frontend` with pages: `New`, `Agents`, `Overview`, `Research`, and per-agent `Monitor`.

Overview metrics are sourced live from the currently selected Alpaca account mode (`paper` or `real`).

## Requirements

- Python 3.11+
- Node.js 20+
- Alpaca paper + live trading credentials
- OpenAI API key

## Backend Setup

1. Create and activate a virtual environment:

   ```bash
   cd backend
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install -e .
   ```

3. Create local environment file:

   ```bash
   cp .env-template .env
   ```

4. Populate `backend/.env`.

## Environment Variables

Core backend vars:

- `ALPACA_PAPER_API_KEY`
- `ALPACA_PAPER_SECRET_KEY`
- `ALPACA_PAPER_BASE_URL` (paper endpoint, default `https://paper-api.alpaca.markets`)
- `ALPACA_REAL_API_KEY`
- `ALPACA_REAL_SECRET_KEY`
- `ALPACA_REAL_BASE_URL` (live endpoint, default `https://api.alpaca.markets`)
- `TRADING_MODE` (`paper` or `real`, default `paper`; persisted in runtime config)
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `DEFAULT_SYMBOL`
- `BAR_TIMEFRAME`
- `FAST_SMA_WINDOW`
- `SLOW_SMA_WINDOW`
- `ORDER_QTY`
- `LOG_LEVEL`

Agent-management vars:

- `AGENT_STORE_PATH` (default: `data/agents.json`)
- `RUNTIME_CONFIG_PATH` (default: `data/runtime_config.json`)
- `RESEARCH_STORE_DIR` (default: `data/research`)
- `RESEARCH_RETENTION_DAYS` (default: `14`)
- `RESEARCH_CANDIDATE_SCAN_LIMIT` (default: `80`)
- `RESEARCH_QUALITY_UNIVERSE_LIMIT` (default: `10`)
- `RESEARCH_MIN_PRICE` (default: `20`)
- `RESEARCH_MIN_DOLLAR_VOLUME` (default: `50000000`)
- `RESEARCH_MIN_AVERAGE_VOLUME` (default: `1000000`)
- `RESEARCH_MIN_VOLUME_RATIO` (default: `0.8`)
- `RESEARCH_MAX_VOLUME_RATIO` (default: `3.0`)
- `AGENT_RUN_INTERVAL_SECONDS` (default: `60`)
- `SUMMARY_STARTING_DEPOSIT` (default: `100`)

## Run Backend API

```bash
cd backend
python -m app.main
```

Default backend URL: `http://0.0.0.0:8000`

Interactive docs: `http://0.0.0.0:8000/docs`

## API Routes

Health and existing trading/research:

- `GET /health`
- `POST /api/v1/trading/buy`
- `POST /api/v1/trading/sell`
- `GET /api/v1/trading/market-data`
- `GET /api/v1/settings/trading-mode`
- `PATCH /api/v1/settings/trading-mode`
- `POST /api/v1/research/strategy`
  - returns `symbols`, `ticker`, `timeframe`, `risk_profile`, `signal_context`, `strategy`, `evaluation`, `research_refs`, `research_thoughts`, optional `quality_findings`
- `GET /api/v1/research/records` list markdown snapshots grouped by scope/date folder
- `GET /api/v1/research/records/{record_id}` fetch one markdown snapshot with metadata + version
- `PATCH /api/v1/research/records/{record_id}` update markdown snapshot content (supports optimistic version checks)
- `DELETE /api/v1/research/records/{record_id}` delete one markdown snapshot
- `POST /api/v1/research/records/delete` bulk delete by `record_ids` or remove a full date-folder via `folder`

Agent routes:

- `POST /api/v1/agents/generate` generate an AI draft (ticker + strategy + defaults) without persisting
- `GET /api/v1/agents/generate/stream` stream live research-thought events and final generated draft payload
- `POST /api/v1/agents` create and persist an agent from a generated draft (`{ generated: ... }` from `/generate`)
- `GET /api/v1/agents` list agents
- `GET /api/v1/agents/{agent_id}` fetch agent by ID across sessions
- `POST /api/v1/agents/{agent_id}/activate` start agent
- `POST /api/v1/agents/{agent_id}/pause` pause agent
- `DELETE /api/v1/agents/{agent_id}` stop an active agent (if needed) and remove it from the JSON store
- `GET /api/v1/agents/{agent_id}/monitor` fetch detailed monitoring data (latest run + recent run details)
- `GET /api/v1/agents/{agent_id}/monitor/stream` live SSE stream of run/status/trade events
- `GET /api/v1/agents/positions` live Alpaca open positions for the active trading mode
- `GET /api/v1/agents/summary?period=day|week|month|all` live Alpaca account overview
- `GET /api/v1/agents/transactions?limit=200&mode=fills` live Alpaca fill activity (newest first)

## Trading Mode Behavior

- Trading mode is controlled globally through backend settings APIs:
  - `GET /api/v1/settings/trading-mode`
  - `PATCH /api/v1/settings/trading-mode` with `{ "trading_mode": "paper" | "real" }`
- The selected mode is persisted in `RUNTIME_CONFIG_PATH` and survives restarts.
- `paper` mode uses `ALPACA_PAPER_*` credentials for all order executions.
- `real` mode uses `ALPACA_REAL_*` credentials for all order executions.
- Historical run records are preserved across mode changes; only new executions use the updated mode.

## Scheduler Behavior

- Scheduler starts automatically with the backend API and checks agents every `AGENT_RUN_INTERVAL_SECONDS`.
- On backend startup, any agent that was previously `active` is auto-paused for safety and marked as pending resume.
- The dashboard prompts once per browser session for selective resume of startup-paused agents.
- Actual execution eligibility is computed per agent:
  - respects each agent's own `interval_seconds` cooldown (`last_run_at + interval_seconds`)
  - applies a timeframe-aware minimum interval floor (for example, `1Day` agents do not run at sub-hour cadence)
  - runs only during Alpaca market-open windows
- During execution, policy checks are enforced:
  - symbol must be included in `execution_plan.symbols`
  - confidence/recommendation gates can block new BUY entries
  - `execution_plan` entry/exit rules are applied when present
  - per-agent guardrails block sells without inventory and cap per-symbol exposure by risk profile
- Agent status remains stable across scheduler re-runs: run completion updates run history/`last_run_at`, while active/paused state changes only through activate/pause actions or safety auto-pause paths.
- On first-ever activation, agents run a one-time bootstrap BUY attempt on the primary ticker (`ticker`, fallback first symbol):
  - this seeds initial inventory so HOLD-first strategies are not stuck with zero holdings
  - bootstrap is attempted once per agent lifecycle and does not repeat on later pause/reactivate cycles
  - bootstrap trades are persisted in `runs` with `policy_action=activation_bootstrap_executed|activation_bootstrap_skipped` and `order_status` details
- If the latest market bar has not changed since the previous run, the trade is skipped with `policy_action=skip_no_new_bar`.
- Live order lifecycle is tracked using broker-reported status transitions. Position state is updated only on filled quantity (`filled` or `partially_filled`), while pending/rejected/canceled orders remain decision records without mutating holdings.
- Agent state and run history are persisted in the JSON store (`AGENT_STORE_PATH`), enabling agent retrieval by ID across sessions.

## Follow-Up Trigger Behavior

- Active agents evaluate follow-up triggers before each cycle:
  - stale research snapshots
  - low-confidence strategy evaluations
  - repeated hold-only streaks
  - portfolio drawdown threshold
- Triggered follow-up refreshes update `signal_context`, `strategy`, `strategy_evaluation`, `execution_plan`, and `research_refs`.
- Runtime follow-up research writes to `runtime` scope, and agent runs sync `research_refs` to the latest ticker-specific runtime record (fallback to generation scope when needed).
- Monitor SSE includes follow-up events:
  - `agent_followup_triggered`
  - `agent_strategy_refreshed`
  - `agent_strategy_refresh_failed`
- Drawdown-triggered refresh failures auto-pause the agent.

## New Agent UX

- Strategy generation is fully automated; no manual symbol/timeframe/interval/name input is required.
- Clicking **Generate Strategy** runs a two-agent pipeline:
  - **Research agent**: fetches active/tradable US equities from Alpaca, computes technical context, enforces strict quality floors (price, dollar volume, average volume, bounded volume ratio), keeps only top quality symbols, and drafts strategy output.
  - **Evaluator agent**: reviews the selected ticker + signal context + strategy draft and returns confidence, recommendation rationale, risk notes, and high-level execution bullets.
- Research context includes liquidity/value signals (for example `dollar_volume_estimate`, `liquidity_tier`, and `price_tier`) to bias generation toward higher-value/higher-volume names while letting the LLM make the final call.
- Quality-universe behavior is configurable:
  - scan up to `RESEARCH_CANDIDATE_SCAN_LIMIT` tradable symbols
  - apply hard quality filters
  - research only the top `RESEARCH_QUALITY_UNIVERSE_LIMIT` quality symbols (default `10`)
- If strict filters produce zero qualified symbols, generation returns a low-conviction fallback draft plus structured `quality_findings` rather than failing the run.
- `quality_findings` include threshold values, pass/fail counts, and symbol-level filter checks to support findings table rendering in the `New` page.
- Strategy picks are event-aware and may include catalyst fields such as `notable_events`, `event_time_horizon`, `event_risk`, and `liquidity_signal`.
- Each generated draft stores markdown research snapshots under split scopes:
  - generation: `data/research/generation/YYYY-MM-DD/`
  - runtime refresh: `data/research/runtime/YYYY-MM-DD/`
- Stored markdown metadata includes compact event-focused hints (`event_focused`, `notable_event_count`, `highest_event_risk`, `recommendation`) for easier research record scanning.
- Strategy generation avoids repeating very recent symbol research when a fresh candidate exists.
- Research retention is automatic: snapshots older than `RESEARCH_RETENTION_DAYS` are pruned during read/write operations.
- Confidence policy is soft-gated:
  - `confidence >= 0.70` => `recommended`
  - `confidence < 0.70` => `not_recommended` (agent creation is allowed)
- The UI shows ticker, timeframe, confidence/recommendation, rationale bullets, plan bullets, and risk notes.
- The `New` page also includes a structured **Research Thought Process** pane sourced from `market_research_service` and `signal_service` outputs (`research_thoughts` in the generated draft payload).
- While **Generate Strategy** is running, the New page streams research/signal thought updates live from `GET /api/v1/agents/generate/stream`, then auto-collapses the pane once the final strategy recommendation arrives (expandable on demand).
- Clicking **Create Agent** sends the generated draft to `POST /api/v1/agents` and persists the agent.
- Create requests enforce dedup checks against existing agents and recent research fingerprints; duplicate strategies return HTTP `409`.
- All broker order submissions use the global trading mode (`paper` or `real`).
- Strategy decisions include BUY/SELL/HOLD:
  - BUY/SELL decisions may submit broker orders.
  - HOLD or policy-blocked decisions are logged without order submission.

## Agents Monitoring UX

- From `Agents`, click **Monitor** on any agent row to open `/agents/{agentId}`.
- The monitor page shows:
  - current status and metadata
  - next scheduled run visibility (`next_run_at` / cooldown / market-closed context)
  - follow-up run response thought process (decision, reasons, actions, next steps)
  - latest run summary and per-symbol decision log entries (executed and non-executed)
  - recent run history
  - a live event feed powered by server-sent events (SSE)
- Controls:
  - **Activate/Pause**
- **Delete** (stops active execution and permanently removes the strategy)
  - **Refresh**

## Overview UX

- The `Overview` page presents account, positions, and transaction data in one place.
- It displays:
  - live account KPIs (portfolio value, equity, cash, buying power, day-change, status/account number)
  - live open positions from Alpaca with unrealized P/L and percentages
  - live fill transaction history from Alpaca, newest first
- Position symbols in the open positions table are clickable strategy drill-ins:
  - single agent match => opens `/agents/{agentId}` directly
  - multiple agent matches => shows an in-page picker to choose which agent strategy monitor to open
  - no agent match => shows an inline message explaining that no strategy is configured for that symbol
- To reduce API load, overview data uses short TTL caches:
  - backend Alpaca response cache: account/positions/fills are cached for a few seconds
  - frontend request cache: in-memory TTL + in-flight dedup for repeated reads
  - clicking **Refresh** bypasses frontend cache and fetches fresh backend data
- `/positions` and `/summary` routes redirect to `/overview`.

## Research UX

- The `Research` page is available at `/research` and is linked in the sidebar.
- The page reads from `data/research` through backend APIs (no direct frontend filesystem access).
- Left pane: date-folder/file browser for markdown snapshots.
- Left pane actions support selecting multiple records and deleting selected entries, or deleting an entire date-folder group.
- Right pane: markdown preview with inline edit mode and save/cancel controls.
- Right pane includes single-record delete for the currently opened document.
- Saves keep the `RESEARCH_META` header at the top of each file and use a document `version` token to prevent stale overwrites.

## Agent Runtime Fields

`AgentResponse` includes market-aware runtime metadata used by `New` and `Agents`:

- `can_run_now`
- `next_run_at`
- `next_run_reason` (`ready`, `paused`, `interval_wait`, `market_closed`)
- `is_market_open`
- `next_market_open`
- `next_market_close`
- `market_status_label`
- `startup_pause_pending`
- `startup_pause_at`
- `startup_pause_source_status`

## Frontend Setup

```bash
cd frontend
npm install
```

Create `frontend/.env.local` (optional):

```bash
NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:8000
```

Run dashboard:

```bash
cd frontend
npm run dev
```

Frontend URL: `http://localhost:3000`

The backend includes CORS for `http://localhost:3000` and `http://127.0.0.1:3000`.

## Project Layout

- `backend/app/services/trading_service.py`: trading operations and signal execution.
- `backend/app/services/market_research_service.py`: strategy generation from market context.
- `backend/app/services/agent_management_service.py`: agent lifecycle and overview-facing account/position/transaction accessors.
- `backend/app/services/strategy_dedup.py`: deterministic strategy fingerprinting for dedupe checks.
- `backend/app/services/agent_scheduler.py`: interval scheduler for active agents.
- `backend/app/repositories/json_agent_store.py`: durable JSON store for agents and runs.
- `backend/app/repositories/markdown_research_store.py`: markdown research history store with retention cleanup.
- `backend/app/api/`: FastAPI routing, schemas, and dependency wiring.
- `frontend/src/app/`: Next.js dashboard pages and shared layout.

## Verification

Backend compile:

```bash
cd backend
python3 -m compileall app
```

Backend tests:

```bash
cd backend
python3 -m pytest -q
```

Test behavior notes:

- Unit tests always run with fixture-backed fake clients.
- Real integration tests live in `backend/tests/integration/` and run in the normal suite when credentials are present (`ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_SECRET_KEY`, `ALPACA_REAL_API_KEY`, `ALPACA_REAL_SECRET_KEY`, `OPENAI_API_KEY`).
- If credentials are missing, integration tests are skipped automatically.
- Integration tests are allowed to execute paper-order flows against Alpaca paper trading.

Frontend lint and build:

```bash
cd frontend
npm run lint
npm run build
```
