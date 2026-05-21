from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MarketBar(BaseModel):
    t: Any
    o: float
    h: float
    l: float
    c: float
    v: float


class OrderRequest(BaseModel):
    symbol: str = Field(min_length=1)
    qty: float | None = Field(default=None, gt=0)
    order_type: str = "market"
    time_in_force: str = "day"


class OrderResponse(BaseModel):
    order: dict[str, Any]


class MarketDataResponse(BaseModel):
    symbol: str
    timeframe: str
    bars: list[MarketBar]
    signal: str
    fast_sma: float
    slow_sma: float
    latest_close: float


class StrategyResearchRequest(BaseModel):
    symbols: list[str] = Field(min_length=1)
    timeframe: str | None = None
    risk_profile: str = "balanced"
    skill_ids: list[str] | None = None


class StrategyResearchResponse(BaseModel):
    symbols: list[str]
    ticker: str
    timeframe: str
    risk_profile: str
    signal_context: list[dict[str, Any]]
    strategy: dict[str, Any]
    evaluation: StrategyEvaluation
    research_refs: list[ResearchReference]
    research_thoughts: list[dict[str, Any]] = Field(default_factory=list)
    quality_findings: QualityFindings | None = None


class StrategyEvaluation(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    recommendation: str
    decision_summary: str
    key_reasons: list[str]
    risk_notes: list[str]
    high_level_plan: list[str]


class QualityFindingThresholds(BaseModel):
    min_price: float
    min_dollar_volume: float
    min_average_volume: float
    min_volume_ratio: float
    max_volume_ratio: float
    quality_universe_limit: int


class QualityFindingRow(BaseModel):
    symbol: str
    signal: str
    latest_close: float
    latest_volume: float
    average_volume: float
    volume_ratio: float
    dollar_volume_estimate: float
    momentum_percent: float
    predictability_score: float
    passed_quality_filters: bool
    failed_checks: list[str] = Field(default_factory=list)


class QualityFindings(BaseModel):
    mode: str
    scan_count: int
    quality_universe_count: int
    thresholds: QualityFindingThresholds
    rows: list[QualityFindingRow] = Field(default_factory=list)


class ResearchReference(BaseModel):
    created_at: str
    file_path: str
    strategy_fingerprint: str
    ticker: str
    timeframe: str
    symbols: list[str]


class ResearchRecordTreeItem(BaseModel):
    id: str
    file_name: str
    created_at: str
    ticker: str
    timeframe: str
    symbols: list[str]
    source: str
    version: str


class ResearchRecordFolder(BaseModel):
    folder: str
    records: list[ResearchRecordTreeItem]


class ResearchRecordTreeResponse(BaseModel):
    folders: list[ResearchRecordFolder]


class ResearchRecordDetailResponse(BaseModel):
    id: str
    file_name: str
    date_folder: str
    relative_path: str
    metadata: dict[str, Any]
    markdown: str
    version: str
    updated_at: str


class ResearchRecordUpdateRequest(BaseModel):
    markdown: str
    expected_version: str | None = None


class ResearchRecordUpdateResponse(BaseModel):
    record: ResearchRecordDetailResponse


class ResearchRecordDeleteResponse(BaseModel):
    record_id: str
    relative_path: str
    ticker: str
    deleted: bool = True


class ResearchRecordsDeleteRequest(BaseModel):
    record_ids: list[str] = Field(default_factory=list)
    folder: str | None = None


class ResearchRecordsDeleteResponse(BaseModel):
    deleted_count: int
    deleted_ids: list[str] = Field(default_factory=list)
    missing_ids: list[str] = Field(default_factory=list)
    folder: str | None = None


class GeneratedAgentDraft(BaseModel):
    name: str
    symbols: list[str] = Field(min_length=1)
    ticker: str
    timeframe: str
    asset_class: Literal["stock", "future"] = "stock"
    risk_profile: str
    interval_seconds: int = Field(ge=5)
    signal_context: list[dict[str, Any]]
    strategy: dict[str, Any]
    strategy_evaluation: StrategyEvaluation
    execution_plan: dict[str, Any]
    research_refs: list[ResearchReference]
    research_thoughts: list[dict[str, Any]] = Field(default_factory=list)
    quality_findings: QualityFindings | None = None
    skill_ids: list[str] = Field(default_factory=list)


class AgentCreateRequest(BaseModel):
    symbols: list[str] | None = None
    timeframe: str | None = None
    asset_class: Literal["stock", "future"] = "stock"
    risk_profile: str | None = None
    interval_seconds: int | None = Field(default=None, ge=5)
    name: str | None = None
    generated: GeneratedAgentDraft | None = None
    skill_ids: list[str] | None = None


class AgentGenerateResponse(BaseModel):
    generated: GeneratedAgentDraft


class AgentRunSummary(BaseModel):
    executed_at: str
    spent: float
    earned: float
    net_pnl: float
    trade_count: int


class AgentRunTrade(BaseModel):
    symbol: str
    signal: str
    side: str
    qty: float
    latest_close: float
    value: float
    order_status: str
    timeframe: str
    policy_action: str | None = None
    policy_reason: str | None = None


class AgentRunDetail(BaseModel):
    executed_at: str
    spent: float
    earned: float
    net_pnl: float
    trades: list[AgentRunTrade]


class AgentResponse(BaseModel):
    agent_id: str
    name: str
    status: str
    startup_pause_pending: bool = False
    startup_pause_at: str | None = None
    startup_pause_source_status: str | None = None
    symbols: list[str]
    ticker: str
    timeframe: str
    asset_class: Literal["stock", "future"] = "stock"
    risk_profile: str
    interval_seconds: int
    signal_context: list[dict[str, Any]]
    strategy: dict[str, Any]
    strategy_evaluation: StrategyEvaluation
    execution_plan: dict[str, Any]
    research_refs: list[ResearchReference]
    created_at: str
    updated_at: str
    last_run_at: str | None
    recent_runs: list[AgentRunSummary]
    can_run_now: bool
    next_run_at: str | None
    next_run_reason: str
    is_market_open: bool
    next_market_open: str | None
    next_market_close: str | None
    market_status_label: str
    skill_ids: list[str] = Field(default_factory=list)
    mission_status: str | None = None
    input_kind: str | None = None
    input_theme: str | None = None
    theme_summary: str = ""
    basket: list[dict[str, Any]] = Field(default_factory=list)
    strategy_window_seconds: int | None = None
    window_rationale: str = ""
    window_started_at: str | None = None
    window_ends_at: str | None = None
    approved_at: str | None = None
    terminal_reason: str | None = None
    generated_skill_id: str | None = None
    decision_log: list[dict[str, Any]] = Field(default_factory=list)


class AgentListResponse(BaseModel):
    agents: list[AgentResponse]


class AgentMonitorEvent(BaseModel):
    event: str
    agent_id: str
    timestamp: str
    payload: dict[str, Any]


class AgentMonitorResponse(BaseModel):
    agent: AgentResponse
    latest_run: AgentRunDetail | None
    recent_runs: list[AgentRunDetail]


class AgentDeleteResponse(BaseModel):
    agent_id: str
    deleted: bool


class PositionItem(BaseModel):
    symbol: str
    qty: float
    side: str
    avg_entry_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    current_price: float
    lastday_price: float
    change_today: float


class PositionsResponse(BaseModel):
    positions: list[PositionItem]


class SummaryResponse(BaseModel):
    period: str
    account_id: str
    account_number: str
    status: str
    buying_power: float
    cash: float
    equity: float
    portfolio_value: float
    long_market_value: float
    short_market_value: float
    last_equity: float
    day_change: float
    day_change_pct: float


class TransactionItem(BaseModel):
    activity_id: str
    activity_type: str
    order_id: str | None = None
    executed_at: str
    symbol: str
    side: str
    qty: float
    price: float
    value: float


class TransactionsResponse(BaseModel):
    transactions: list[TransactionItem]


class TradingModeResponse(BaseModel):
    trading_mode: str = Field(pattern="^(paper|real)$")


class TradingModeUpdateRequest(BaseModel):
    trading_mode: str = Field(pattern="^(paper|real)$")


class ProviderCapabilityResponse(BaseModel):
    id: str
    label: str
    market_data: bool
    trading: bool
    asset_classes: list[str]
    supports_paper: bool
    description: str = ""


class ProviderSelectionResponse(BaseModel):
    data_provider: str
    broker_provider: str
    providers: list[ProviderCapabilityResponse]


class ProviderSelectionUpdateRequest(BaseModel):
    data_provider: str | None = None
    broker_provider: str | None = None


class SkillSummary(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    source: str
    created_at: str
    updated_at: str
    version: str
    file_count: int
    total_bytes: int


class SkillListResponse(BaseModel):
    skills: list[SkillSummary] = Field(default_factory=list)


class SkillFileItem(BaseModel):
    path: str
    bytes: int


class SkillDetailResponse(SkillSummary):
    markdown: str
    files: list[SkillFileItem] = Field(default_factory=list)


class SkillFileResponse(BaseModel):
    path: str
    content: str


class SkillEditorCreateRequest(BaseModel):
    name: str | None = None
    markdown: str = Field(min_length=1)


class SkillUpdateRequest(BaseModel):
    markdown: str = Field(min_length=1)
    expected_version: str | None = None


class SkillDeleteResponse(BaseModel):
    skill_id: str
    deleted: bool = True


class AgentSkillsUpdateRequest(BaseModel):
    skill_ids: list[str] = Field(default_factory=list)


class MissionGenerateRequest(BaseModel):
    symbols: list[str] | None = None
    theme: str | None = None
    timeframe: str | None = None
    asset_class: Literal["stock", "future"] = "stock"
    risk_profile: str | None = None
    skill_ids: list[str] | None = None


class MissionGenerateResponse(BaseModel):
    draft: dict[str, Any]


class MissionCreateRequest(BaseModel):
    draft: dict[str, Any]
    skill_ids: list[str] | None = None


class MissionApproveRequest(BaseModel):
    window_seconds: int | None = Field(default=None, ge=60)
