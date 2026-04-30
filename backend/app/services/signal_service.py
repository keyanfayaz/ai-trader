from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SignalResult:
    signal: str
    fast_sma: float
    slow_sma: float
    latest_close: float
    previous_fast_sma: float
    previous_slow_sma: float
    bar_count: int


@dataclass(slots=True)
class MarketSignalSummary:
    signal_result: SignalResult
    latest_volume: float
    average_volume: float
    volume_ratio: float
    momentum_percent: float


def calculate_sma_signal(
    closes: list[float],
    fast_window: int,
    slow_window: int,
) -> SignalResult:
    """Calculate a simple SMA crossover signal from a close-price series."""
    minimum_bars = slow_window + 1
    if len(closes) < minimum_bars:
        raise ValueError(
            f"Need at least {minimum_bars} bars to calculate the SMA crossover signal."
        )

    latest_fast = _sma(closes[-fast_window:])
    latest_slow = _sma(closes[-slow_window:])
    previous_fast = _sma(closes[-(fast_window + 1):-1])
    previous_slow = _sma(closes[-(slow_window + 1):-1])

    signal = "HOLD"
    if previous_fast <= previous_slow and latest_fast > latest_slow:
        signal = "BUY"
    elif previous_fast >= previous_slow and latest_fast < latest_slow:
        signal = "SELL"

    return SignalResult(
        signal=signal,
        fast_sma=latest_fast,
        slow_sma=latest_slow,
        latest_close=closes[-1],
        previous_fast_sma=previous_fast,
        previous_slow_sma=previous_slow,
        bar_count=len(closes),
    )


def _sma(values: list[float]) -> float:
    return sum(values) / len(values)


def summarize_market_signal(
    bars: list[dict[str, float]],
    fast_window: int,
    slow_window: int,
    volume_window: int = 20,
) -> MarketSignalSummary:
    closes = [float(bar["c"]) for bar in bars]
    volumes = [float(bar["v"]) for bar in bars]
    signal_result = calculate_sma_signal(
        closes=closes,
        fast_window=fast_window,
        slow_window=slow_window,
    )

    recent_volumes = volumes[-min(volume_window, len(volumes)) :]
    average_volume = _sma(recent_volumes)
    latest_volume = volumes[-1]
    first_close = closes[0]
    momentum_percent = ((signal_result.latest_close - first_close) / first_close) * 100.0

    return MarketSignalSummary(
        signal_result=signal_result,
        latest_volume=latest_volume,
        average_volume=average_volume,
        volume_ratio=latest_volume / average_volume if average_volume > 0 else 0.0,
        momentum_percent=momentum_percent,
    )
