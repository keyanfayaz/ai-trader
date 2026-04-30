# Auto Trader Frontend

Next.js dashboard for managing AI-generated trading agents.

## Requirements

- Node.js 20+
- Backend API running (default: `http://127.0.0.1:8000`)

## Setup

```bash
npm install
```

Optional environment file (`.env.local`):

```bash
NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:8000
```

## Run

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Dashboard Pages

- `/new`: Generate a strategy draft using research + evaluator agents, review recommendation details, then create an agent.
- `/agents`: View agents, next scheduled run state, activate/pause controls, and follow-up thought stream updates.
- `/agents/[agentId]`: Monitor a specific agent with next scheduled run timing, run response thought process, live events, and run history.
- `/overview`: View live account summary, open positions, transactions, and global follow-up thought stream.

## API Usage

The `New` page uses:

- `POST /api/v1/agents/generate` to preview a generated draft.
- `POST /api/v1/agents` with `{ generated: ... }` to persist that draft as an agent.

Generated strategy preview includes:

- ticker, timeframe, risk profile, and interval
- evaluator confidence + recommendation
- decision summary
- key reasons, high-level plan bullets, and risk notes
- execution timing explanation:
  - strategy runs are gated by market-open windows
  - broker submissions route through global trading mode (`paper` or `real`)

Soft-gate behavior:

- confidence under 70% is labeled `Not Recommended`
- user can still create the agent (explicitly non-blocking)

The `Monitor` page uses:

- `GET /api/v1/agents/{agentId}/monitor` for initial hydration.
- `GET /api/v1/agents/{agentId}/monitor/stream` as an SSE live stream.
- `POST /api/v1/agents/{agentId}/activate` and `POST /api/v1/agents/{agentId}/pause` for controls.

## Verification

```bash
npm run lint
npm run build
```
