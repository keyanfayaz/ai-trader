import type {
  Agent,
  AgentDeleteResponse,
  AgentFollowUpEvent,
  AgentGenerateResponse,
  AgentListResponse,
  AgentMonitorEvent,
  AgentMonitorResponse,
  CreateAgentPayload,
  PositionsResponse,
  ResearchRecordDetail,
  ResearchRecordDeleteResponse,
  ResearchRecordsDeleteResponse,
  ResearchRecordTreeResponse,
  ResearchRecordUpdateResponse,
  SummaryResponse,
  TradingMode,
  TradingModeResponse,
  TransactionsResponse,
} from "@/lib/types";

const BASE_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://127.0.0.1:8000";

type RequestCacheOptions = {
  cacheTtlMs?: number;
  forceRefresh?: boolean;
};

const responseCache = new Map<string, { expiresAt: number; data: unknown }>();
const inFlightRequests = new Map<string, Promise<unknown>>();

async function request<T>(path: string, init?: RequestInit, options?: RequestCacheOptions): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const cacheable = method === "GET" && !!options?.cacheTtlMs && options.cacheTtlMs > 0;
  const cacheKey = `${method}:${path}`;
  const now = Date.now();

  if (cacheable && !options?.forceRefresh) {
    const cached = responseCache.get(cacheKey);
    if (cached && cached.expiresAt > now) {
      return cached.data as T;
    }
    const inFlight = inFlightRequests.get(cacheKey);
    if (inFlight) {
      return (await inFlight) as T;
    }
  }

  const task = (async () => {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Backend request failed (${response.status}): ${body}`);
  }
    const payload = (await response.json()) as T;
    if (cacheable) {
      responseCache.set(cacheKey, {
        expiresAt: now + (options?.cacheTtlMs ?? 0),
        data: payload,
      });
    }
    return payload;
  })();

  if (cacheable) {
    inFlightRequests.set(cacheKey, task as Promise<unknown>);
  }
  try {
    return await task;
  } finally {
    if (cacheable) {
      inFlightRequests.delete(cacheKey);
    }
  }
}

export function createAgent(payload: CreateAgentPayload): Promise<Agent> {
  return request<Agent>("/api/v1/agents", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function generateAgentStrategy(): Promise<AgentGenerateResponse> {
  return request<AgentGenerateResponse>("/api/v1/agents/generate", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

type GenerateStreamCallbacks = {
  onThought: (thought: Record<string, unknown>) => void;
  onCompleted: (response: AgentGenerateResponse) => void;
  onFailed: (error: string) => void;
  onError?: (error: string) => void;
};

export function streamGenerateAgentStrategy(callbacks: GenerateStreamCallbacks): () => void {
  const source = new EventSource(`${BASE_URL}/api/v1/agents/generate/stream`);
  let finished = false;

  const thoughtHandler: EventListener = (rawEvent) => {
    const event = rawEvent as MessageEvent<string>;
    try {
      const payload = JSON.parse(event.data) as { payload?: Record<string, unknown> };
      callbacks.onThought(payload.payload ?? {});
    } catch {
      callbacks.onError?.("Unable to parse research thought event.");
    }
  };
  const completedHandler: EventListener = (rawEvent) => {
    const event = rawEvent as MessageEvent<string>;
    try {
      const payload = JSON.parse(event.data) as { payload?: AgentGenerateResponse };
      if (!payload.payload) {
        callbacks.onFailed("Generate stream completed without payload.");
        return;
      }
      callbacks.onCompleted(payload.payload);
    } catch {
      callbacks.onFailed("Unable to parse generate completion payload.");
    } finally {
      finished = true;
      source.close();
    }
  };
  const failedHandler: EventListener = (rawEvent) => {
    const event = rawEvent as MessageEvent<string>;
    try {
      const payload = JSON.parse(event.data) as { payload?: { error?: string } };
      callbacks.onFailed(payload.payload?.error ?? "Strategy generation failed.");
    } catch {
      callbacks.onFailed("Strategy generation failed.");
    } finally {
      finished = true;
      source.close();
    }
  };

  source.addEventListener("research_thought", thoughtHandler);
  source.addEventListener("generate_completed", completedHandler);
  source.addEventListener("generate_failed", failedHandler);
  source.onerror = () => {
    if (finished || source.readyState === EventSource.CLOSED) {
      return;
    }
    callbacks.onError?.("Generate stream disconnected.");
  };

  return () => {
    finished = true;
    source.removeEventListener("research_thought", thoughtHandler);
    source.removeEventListener("generate_completed", completedHandler);
    source.removeEventListener("generate_failed", failedHandler);
    source.close();
  };
}

export function listAgents(): Promise<AgentListResponse> {
  return request<AgentListResponse>("/api/v1/agents");
}

export function activateAgent(agentId: string): Promise<Agent> {
  return request<Agent>(`/api/v1/agents/${agentId}/activate`, { method: "POST" });
}

export function pauseAgent(agentId: string): Promise<Agent> {
  return request<Agent>(`/api/v1/agents/${agentId}/pause`, { method: "POST" });
}

export function deleteAgent(agentId: string): Promise<AgentDeleteResponse> {
  return request<AgentDeleteResponse>(`/api/v1/agents/${agentId}`, { method: "DELETE" });
}

export function getAgentMonitor(agentId: string): Promise<AgentMonitorResponse> {
  return request<AgentMonitorResponse>(`/api/v1/agents/${agentId}/monitor`);
}

export function getPositions(options?: RequestCacheOptions): Promise<PositionsResponse> {
  return request<PositionsResponse>("/api/v1/agents/positions", undefined, { cacheTtlMs: 8_000, ...options });
}

export function getSummary(period: SummaryResponse["period"], options?: RequestCacheOptions): Promise<SummaryResponse> {
  return request<SummaryResponse>(`/api/v1/agents/summary?period=${period}`, undefined, {
    cacheTtlMs: 10_000,
    ...options,
  });
}

export function getTransactions(limit = 200, options?: RequestCacheOptions): Promise<TransactionsResponse> {
  return request<TransactionsResponse>(`/api/v1/agents/transactions?limit=${limit}&mode=fills`, undefined, {
    cacheTtlMs: 15_000,
    ...options,
  });
}

export function listResearchRecords(): Promise<ResearchRecordTreeResponse> {
  return request<ResearchRecordTreeResponse>("/api/v1/research/records");
}

export function getResearchRecord(recordId: string): Promise<ResearchRecordDetail> {
  return request<ResearchRecordDetail>(`/api/v1/research/records/${recordId}`);
}

export function updateResearchRecord(
  recordId: string,
  markdown: string,
  expectedVersion: string | null,
): Promise<ResearchRecordUpdateResponse> {
  return request<ResearchRecordUpdateResponse>(`/api/v1/research/records/${recordId}`, {
    method: "PATCH",
    body: JSON.stringify({
      markdown,
      expected_version: expectedVersion,
    }),
  });
}

export function deleteResearchRecord(recordId: string): Promise<ResearchRecordDeleteResponse> {
  return request<ResearchRecordDeleteResponse>(`/api/v1/research/records/${recordId}`, {
    method: "DELETE",
  });
}

export function deleteResearchRecords(recordIds: string[]): Promise<ResearchRecordsDeleteResponse> {
  return request<ResearchRecordsDeleteResponse>("/api/v1/research/records/delete", {
    method: "POST",
    body: JSON.stringify({ record_ids: recordIds }),
  });
}

export function deleteResearchFolder(folder: string): Promise<ResearchRecordsDeleteResponse> {
  return request<ResearchRecordsDeleteResponse>("/api/v1/research/records/delete", {
    method: "POST",
    body: JSON.stringify({ folder }),
  });
}

export function getTradingMode(): Promise<TradingModeResponse> {
  return request<TradingModeResponse>("/api/v1/settings/trading-mode");
}

export function setTradingMode(tradingMode: TradingMode): Promise<TradingModeResponse> {
  return request<TradingModeResponse>("/api/v1/settings/trading-mode", {
    method: "PATCH",
    body: JSON.stringify({ trading_mode: tradingMode }),
  });
}

type MonitorStreamCallbacks = {
  onEvent: (event: AgentMonitorEvent) => void;
  onError?: (error: string) => void;
};

const MONITOR_EVENT_NAMES = [
  "agent_status_changed",
  "agent_run_started",
  "agent_run_skipped",
  "agent_trade_decision",
  "agent_run_completed",
  "agent_run_failed",
  "agent_followup_triggered",
  "agent_strategy_refreshed",
  "agent_strategy_refresh_failed",
] as const;

const FOLLOWUP_EVENT_NAMES = [
  "agent_followup_triggered",
  "agent_strategy_refreshed",
  "agent_strategy_refresh_failed",
] as const;

export function streamAgentMonitor(agentId: string, callbacks: MonitorStreamCallbacks): () => void {
  const source = new EventSource(`${BASE_URL}/api/v1/agents/${agentId}/monitor/stream`);
  const handlers: Array<[string, EventListener]> = [];

  for (const eventName of MONITOR_EVENT_NAMES) {
    const handler: EventListener = (rawEvent) => {
      const event = rawEvent as MessageEvent<string>;
      try {
        callbacks.onEvent(JSON.parse(event.data) as AgentMonitorEvent);
      } catch {
        callbacks.onError?.("Unable to parse monitor stream event.");
      }
    };
    source.addEventListener(eventName, handler);
    handlers.push([eventName, handler]);
  }

  source.onerror = () => {
    callbacks.onError?.("Monitor stream disconnected. Attempting to reconnect...");
  };

  return () => {
    for (const [eventName, handler] of handlers) {
      source.removeEventListener(eventName, handler);
    }
    source.close();
  };
}

export function streamAllAgentMonitor(callbacks: MonitorStreamCallbacks): () => void {
  const source = new EventSource(`${BASE_URL}/api/v1/agents/monitor/stream`);
  const handlers: Array<[string, EventListener]> = [];

  for (const eventName of MONITOR_EVENT_NAMES) {
    const handler: EventListener = (rawEvent) => {
      const event = rawEvent as MessageEvent<string>;
      try {
        callbacks.onEvent(JSON.parse(event.data) as AgentMonitorEvent);
      } catch {
        callbacks.onError?.("Unable to parse monitor stream event.");
      }
    };
    source.addEventListener(eventName, handler);
    handlers.push([eventName, handler]);
  }

  source.onerror = () => {
    callbacks.onError?.("Monitor stream disconnected. Attempting to reconnect...");
  };

  return () => {
    for (const [eventName, handler] of handlers) {
      source.removeEventListener(eventName, handler);
    }
    source.close();
  };
}

type FollowUpStreamCallbacks = {
  onEvent: (event: AgentFollowUpEvent) => void;
  onError?: (error: string) => void;
};

export function streamFollowUpThoughts(callbacks: FollowUpStreamCallbacks): () => void {
  const source = new EventSource(`${BASE_URL}/api/v1/agents/monitor/stream`);
  const handlers: Array<[string, EventListener]> = [];

  for (const eventName of FOLLOWUP_EVENT_NAMES) {
    const handler: EventListener = (rawEvent) => {
      const event = rawEvent as MessageEvent<string>;
      try {
        callbacks.onEvent(JSON.parse(event.data) as AgentFollowUpEvent);
      } catch {
        callbacks.onError?.("Unable to parse follow-up thought stream event.");
      }
    };
    source.addEventListener(eventName, handler);
    handlers.push([eventName, handler]);
  }

  source.onerror = () => {
    callbacks.onError?.("Follow-up thought stream disconnected. Attempting to reconnect...");
  };

  return () => {
    for (const [eventName, handler] of handlers) {
      source.removeEventListener(eventName, handler);
    }
    source.close();
  };
}
