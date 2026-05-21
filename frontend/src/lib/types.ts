export type AgentRunSummary = {
  executed_at: string;
  spent: number;
  earned: number;
  net_pnl: number;
  trade_count: number;
};

export type AgentRunTrade = {
  symbol: string;
  signal: string;
  side: string;
  qty: number;
  latest_close: number;
  value: number;
  order_status: string;
  timeframe: string;
  policy_action?: string | null;
  policy_reason?: string | null;
};

export type AgentRunDetail = {
  executed_at: string;
  spent: number;
  earned: number;
  net_pnl: number;
  trades: AgentRunTrade[];
};

export type AssetClass = "stock" | "future";

export type Agent = {
  agent_id: string;
  name: string;
  status: "active" | "paused";
  startup_pause_pending: boolean;
  startup_pause_at: string | null;
  startup_pause_source_status: string | null;
  symbols: string[];
  ticker: string;
  timeframe: string;
  asset_class: AssetClass;
  risk_profile: string;
  interval_seconds: number;
  signal_context: Array<Record<string, unknown>>;
  strategy: Record<string, unknown>;
  strategy_evaluation: StrategyEvaluation;
  execution_plan: Record<string, unknown>;
  research_refs: ResearchReference[];
  created_at: string;
  updated_at: string;
  last_run_at: string | null;
  recent_runs: AgentRunSummary[];
  can_run_now: boolean;
  next_run_at: string | null;
  next_run_reason: string;
  is_market_open: boolean;
  next_market_open: string | null;
  next_market_close: string | null;
  market_status_label: string;
  skill_ids: string[];
  mission_status?: MissionStatus | null;
  input_kind?: string | null;
  input_theme?: string | null;
  theme_summary?: string;
  basket?: BasketPick[];
  strategy_window_seconds?: number | null;
  window_rationale?: string;
  window_started_at?: string | null;
  window_ends_at?: string | null;
  approved_at?: string | null;
  terminal_reason?: string | null;
  generated_skill_id?: string | null;
  decision_log?: DecisionLogEntry[];
};

export type MissionStatus =
  | "awaiting_approval"
  | "armed"
  | "executing"
  | "completed"
  | "rejected";

export type BasketPick = {
  symbol: string;
  rationale: string;
  score: number;
};

export type ExecutionPlanLeg = {
  symbol: string;
  entry: { type: string; ref_price: number | null; condition: string };
  target_pct: number;
  stop_pct: number;
  weight: number;
};

export type MissionExecutionPlan = {
  recommended_window_seconds: number;
  window_rationale: string;
  plan_notes: string;
  legs: ExecutionPlanLeg[];
};

export type DecisionLogEntry = {
  at: string;
  phase: string;
  symbol: string;
  action: string;
  reason: string;
  order_id?: string | null;
  filled_qty?: number | null;
  filled_avg_price?: number | null;
};

export type MissionDraft = {
  name: string;
  symbols: string[];
  ticker: string;
  timeframe: string;
  asset_class?: AssetClass;
  risk_profile: string;
  input_kind: string;
  input_theme: string | null;
  theme_summary: string;
  basket: BasketPick[];
  signal_context: Array<Record<string, unknown>>;
  strategy: Record<string, unknown>;
  strategy_evaluation: StrategyEvaluation;
  execution_plan: MissionExecutionPlan;
  window_seconds: number;
  window_rationale: string;
  skill_markdown: string;
  research_refs: ResearchReference[];
  research_thoughts?: Array<Record<string, unknown>>;
  preset_skill_ids?: string[];
};

export type MissionGenerateResponse = {
  draft: MissionDraft;
};

export type AgentListResponse = {
  agents: Agent[];
};

export type AgentDeleteResponse = {
  agent_id: string;
  deleted: boolean;
};

export type AgentMonitorEvent = {
  event: string;
  agent_id: string;
  timestamp: string;
  payload: Record<string, unknown>;
};

export type AgentThoughtSummary = {
  stage: "triggered" | "refreshed" | "failed" | string;
  reasons: string[];
  reason_labels: string[];
  decision: string;
  actions: string[];
  next_steps: string[];
};

export type AgentFollowUpEvent = AgentMonitorEvent & {
  event: "agent_followup_triggered" | "agent_strategy_refreshed" | "agent_strategy_refresh_failed";
  payload: {
    reasons?: string[];
    ticker?: string;
    symbols?: string[];
    error?: string;
    thought_summary?: AgentThoughtSummary;
    [key: string]: unknown;
  };
};

export type AgentMonitorResponse = {
  agent: Agent;
  latest_run: AgentRunDetail | null;
  recent_runs: AgentRunDetail[];
};

export type PositionsResponse = {
  positions: Array<{
    symbol: string;
    side: string;
    qty: number;
    avg_entry_price: number;
    market_value: number;
    unrealized_pnl: number;
    unrealized_pnl_pct: number;
    current_price: number;
    lastday_price: number;
    change_today: number;
  }>;
};

export type SummaryResponse = {
  period: "day" | "week" | "month" | "all";
  account_id: string;
  account_number: string;
  status: string;
  buying_power: number;
  cash: number;
  equity: number;
  portfolio_value: number;
  long_market_value: number;
  short_market_value: number;
  last_equity: number;
  day_change: number;
  day_change_pct: number;
};

export type TransactionItem = {
  activity_id: string;
  activity_type: string;
  order_id?: string | null;
  executed_at: string;
  symbol: string;
  side: "buy" | "sell" | string;
  qty: number;
  price: number;
  value: number;
};

export type TransactionsResponse = {
  transactions: TransactionItem[];
};

export type CreateAgentPayload = {
  symbols?: string[];
  timeframe?: string;
  risk_profile?: string;
  interval_seconds?: number;
  name?: string;
  generated?: GeneratedAgentDraft;
  skill_ids?: string[];
};

export type GeneratedAgentDraft = {
  name: string;
  symbols: string[];
  ticker: string;
  timeframe: string;
  risk_profile: string;
  interval_seconds: number;
  signal_context: Array<Record<string, unknown>>;
  strategy: Record<string, unknown>;
  strategy_evaluation: StrategyEvaluation;
  execution_plan: Record<string, unknown>;
  research_refs: ResearchReference[];
  research_thoughts?: Array<Record<string, unknown>>;
  quality_findings?: QualityFindings | null;
  skill_ids?: string[];
};

export type AgentGenerateResponse = {
  generated: GeneratedAgentDraft;
};

export type ResearchRecordTreeItem = {
  id: string;
  file_name: string;
  created_at: string;
  ticker: string;
  timeframe: string;
  symbols: string[];
  source: string;
  version: string;
};

export type ResearchRecordFolder = {
  folder: string;
  records: ResearchRecordTreeItem[];
};

export type ResearchRecordTreeResponse = {
  folders: ResearchRecordFolder[];
};

export type ResearchRecordDetail = {
  id: string;
  file_name: string;
  date_folder: string;
  relative_path: string;
  metadata: Record<string, unknown>;
  markdown: string;
  version: string;
  updated_at: string;
};

export type ResearchRecordUpdateResponse = {
  record: ResearchRecordDetail;
};

export type ResearchRecordDeleteResponse = {
  record_id: string;
  relative_path: string;
  ticker: string;
  deleted: boolean;
};

export type ResearchRecordsDeleteResponse = {
  deleted_count: number;
  deleted_ids: string[];
  missing_ids: string[];
  folder?: string | null;
};

export type StrategyEvaluation = {
  confidence: number;
  threshold: number;
  recommendation: "recommended" | "not_recommended";
  decision_summary: string;
  key_reasons: string[];
  risk_notes: string[];
  high_level_plan: string[];
};

export type ResearchReference = {
  created_at: string;
  file_path: string;
  strategy_fingerprint: string;
  ticker: string;
  timeframe: string;
  symbols: string[];
};

export type QualityFindingThresholds = {
  min_price: number;
  min_dollar_volume: number;
  min_average_volume: number;
  min_volume_ratio: number;
  max_volume_ratio: number;
  quality_universe_limit: number;
};

export type QualityFindingRow = {
  symbol: string;
  signal: string;
  latest_close: number;
  latest_volume: number;
  average_volume: number;
  volume_ratio: number;
  dollar_volume_estimate: number;
  momentum_percent: number;
  predictability_score: number;
  passed_quality_filters: boolean;
  failed_checks: string[];
};

export type QualityFindings = {
  mode: "strict_quality_passed" | "strict_quality_fallback" | string;
  scan_count: number;
  quality_universe_count: number;
  thresholds: QualityFindingThresholds;
  rows: QualityFindingRow[];
};

export type SkillSummary = {
  id: string;
  name: string;
  slug: string;
  description: string;
  source: "upload" | "editor" | string;
  created_at: string;
  updated_at: string;
  version: string;
  file_count: number;
  total_bytes: number;
};

export type SkillFileItem = {
  path: string;
  bytes: number;
};

export type SkillDetail = SkillSummary & {
  markdown: string;
  files: SkillFileItem[];
};

export type SkillListResponse = {
  skills: SkillSummary[];
};

export type TradingMode = "paper" | "real";

export type TradingModeResponse = {
  trading_mode: TradingMode;
};

export type ProviderCapability = {
  id: string;
  label: string;
  market_data: boolean;
  trading: boolean;
  asset_classes: string[];
  supports_paper: boolean;
  description: string;
};

export type ProviderSelection = {
  data_provider: string;
  broker_provider: string;
  providers: ProviderCapability[];
};
