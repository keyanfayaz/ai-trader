from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from threading import Lock
import time
from typing import Any

from alpaca.data.enums import DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest


@dataclass(slots=True)
class AlpacaClient:
    api_key: str
    secret_key: str
    trading_base_url: str
    paper: bool = True
    data_client: Any | None = None
    trading_client: Any | None = None

    def __post_init__(self) -> None:
        if self.data_client is None:
            self.data_client = StockHistoricalDataClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
            )
        if self.trading_client is None:
            self.trading_client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=self.paper,
                url_override=_normalize_trading_base_url(self.trading_base_url),
            )

    def get_bars(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=_estimate_lookback_days(limit, timeframe))
            request = StockBarsRequest(
                symbol_or_symbols=symbol.upper(),
                timeframe=_parse_timeframe(timeframe),
                start=start,
                end=end,
                limit=limit,
                feed=DataFeed.IEX,
            )
            bars_response = self.data_client.get_stock_bars(request)
        except Exception as exc:
            raise RuntimeError(f"Alpaca market data request failed for {symbol}.") from exc

        symbol_upper = symbol.upper()
        bars = getattr(bars_response, "data", {}).get(symbol_upper)
        if not bars:
            raise RuntimeError(
                f"Alpaca market data response did not include bars for symbol '{symbol_upper}'."
            )

        return [
            {
                "t": bar.timestamp,
                "o": float(bar.open),
                "h": float(bar.high),
                "l": float(bar.low),
                "c": float(bar.close),
                "v": float(bar.volume),
            }
            for bar in bars
        ]

    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        if order_type.lower() != "market":
            raise ValueError("Only market orders are currently supported.")
        try:
            order_request = MarketOrderRequest(
                symbol=symbol.upper(),
                qty=qty,
                side=OrderSide[side.upper()],
                time_in_force=TimeInForce[time_in_force.upper()],
            )
            order = self.trading_client.submit_order(order_request)
            return {
                "id": str(order.id),
                "symbol": order.symbol,
                "qty": float(order.qty),
                "side": str(order.side),
                "type": str(order.order_type),
                "time_in_force": str(order.time_in_force),
                "status": str(order.status),
                "filled_qty": _safe_float(getattr(order, "filled_qty", 0.0)),
                "filled_avg_price": _safe_float(getattr(order, "filled_avg_price", 0.0)),
            }
        except Exception as exc:
            raise RuntimeError(f"Alpaca order request failed for {symbol}.") from exc

    def get_order(self, order_id: str) -> dict[str, Any]:
        try:
            order = self.trading_client.get_order_by_id(order_id)
            return {
                "id": str(order.id),
                "symbol": str(order.symbol),
                "qty": _safe_float(getattr(order, "qty", 0.0)),
                "side": str(getattr(order, "side", "")),
                "type": str(getattr(order, "order_type", "")),
                "time_in_force": str(getattr(order, "time_in_force", "")),
                "status": str(getattr(order, "status", "")),
                "filled_qty": _safe_float(getattr(order, "filled_qty", 0.0)),
                "filled_avg_price": _safe_float(getattr(order, "filled_avg_price", 0.0)),
            }
        except Exception as exc:
            raise RuntimeError(f"Alpaca order status request failed for {order_id}.") from exc

    def get_account(self) -> dict[str, Any]:
        return self.get_account_overview()

    def get_account_overview(self) -> dict[str, Any]:
        cache_key = _build_cache_key(self, "account_overview")
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            account = self.trading_client.get_account()
            payload = {
                "id": str(account.id),
                "account_number": str(getattr(account, "account_number", "")),
                "status": str(account.status),
                "buying_power": _safe_float(getattr(account, "buying_power", 0.0)),
                "cash": _safe_float(getattr(account, "cash", 0.0)),
                "equity": _safe_float(getattr(account, "equity", 0.0)),
                "portfolio_value": _safe_float(getattr(account, "portfolio_value", getattr(account, "equity", 0.0))),
                "long_market_value": _safe_float(getattr(account, "long_market_value", 0.0)),
                "short_market_value": _safe_float(getattr(account, "short_market_value", 0.0)),
                "last_equity": _safe_float(getattr(account, "last_equity", 0.0)),
                "day_change": _safe_float(getattr(account, "equity", 0.0))
                - _safe_float(getattr(account, "last_equity", 0.0)),
                "day_change_pct": _safe_float(getattr(account, "equity", 0.0))
                / _safe_float(getattr(account, "last_equity", 1.0))
                - 1.0
                if _safe_float(getattr(account, "last_equity", 0.0)) > 0
                else 0.0,
            }
            _cache_set(cache_key, payload, ttl_seconds=_ACCOUNT_OVERVIEW_TTL_SECONDS)
            return payload
        except Exception as exc:
            raise RuntimeError("Alpaca account request failed.") from exc

    def get_open_positions(self) -> list[dict[str, Any]]:
        cache_key = _build_cache_key(self, "open_positions")
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            raw_positions = self.trading_client.get_all_positions()
        except Exception as exc:
            raise RuntimeError("Alpaca positions request failed.") from exc

        positions: list[dict[str, Any]] = []
        for position in raw_positions:
            symbol = str(getattr(position, "symbol", "")).upper().strip()
            if not symbol:
                continue
            positions.append(
                {
                    "symbol": symbol,
                    "qty": _safe_float(getattr(position, "qty", 0.0)),
                    "side": _safe_str(getattr(position, "side", "")),
                    "avg_entry_price": _safe_float(getattr(position, "avg_entry_price", 0.0)),
                    "market_value": _safe_float(getattr(position, "market_value", 0.0)),
                    "unrealized_pnl": _safe_float(getattr(position, "unrealized_pl", 0.0)),
                    "unrealized_pnl_pct": _safe_float(getattr(position, "unrealized_plpc", 0.0)),
                    "current_price": _safe_float(getattr(position, "current_price", 0.0)),
                    "lastday_price": _safe_float(getattr(position, "lastday_price", 0.0)),
                    "change_today": _safe_float(getattr(position, "change_today", 0.0)),
                }
            )
        result = sorted(positions, key=lambda item: item["symbol"])
        _cache_set(cache_key, result, ttl_seconds=_OPEN_POSITIONS_TTL_SECONDS)
        return result

    def get_account_activities(
        self,
        activity_types: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 1000))
        normalized_activity_types = [item.strip().upper() for item in (activity_types or ["FILL"]) if item.strip()]
        activities_cache_key = _build_cache_key(self, f"activities:{','.join(normalized_activity_types)}")
        cached_bucket = _cache_get(activities_cache_key)
        if isinstance(cached_bucket, dict):
            cached_limit = int(cached_bucket.get("fetched_limit", 0) or 0)
            cached_rows = cached_bucket.get("rows")
            if isinstance(cached_rows, list) and cached_limit >= normalized_limit:
                return cached_rows[:normalized_limit]

        fetch_limit = max(
            normalized_limit,
            int(cached_bucket.get("fetched_limit", 0) or 0) if isinstance(cached_bucket, dict) else 0,
        )
        normalized: list[dict[str, Any]] = []
        page_token: str | None = None
        remaining = fetch_limit

        while remaining > 0:
            page_size = min(remaining, 100)
            params = {
                "activity_types": ",".join(normalized_activity_types),
                "direction": "desc",
                "page_size": str(page_size),
            }
            if page_token:
                params["page_token"] = page_token
            try:
                # alpaca-py trading client does not expose this endpoint as a typed helper in all versions.
                activities = self.trading_client.get("/account/activities", params)
            except Exception as exc:
                raise RuntimeError("Alpaca account activities request failed.") from exc

            if not isinstance(activities, list) or not activities:
                break

            for activity in activities:
                normalized_item = _normalize_activity_record(activity)
                if not normalized_item:
                    continue
                normalized.append(normalized_item)
                remaining -= 1
                if remaining <= 0:
                    break

            if len(activities) < page_size or remaining <= 0:
                break

            page_token = _extract_activity_page_token(activities[-1])
            if not page_token:
                break

        normalized.sort(key=lambda item: item["executed_at"], reverse=True)
        _cache_set(
            activities_cache_key,
            {
                "fetched_limit": fetch_limit,
                "rows": normalized[:fetch_limit],
            },
            ttl_seconds=_ACCOUNT_ACTIVITIES_TTL_SECONDS,
        )
        return normalized[:normalized_limit]

    def list_active_tradable_equity_symbols(self, limit: int = 200) -> list[str]:
        try:
            assets = self.trading_client.get_all_assets()
        except Exception as exc:
            raise RuntimeError("Alpaca assets request failed.") from exc

        symbols: list[str] = []
        for asset in assets:
            symbol = str(getattr(asset, "symbol", "")).upper().strip()
            if not symbol:
                continue

            status_value = str(getattr(getattr(asset, "status", None), "value", getattr(asset, "status", ""))).lower()
            asset_class_value = str(
                getattr(getattr(asset, "asset_class", None), "value", getattr(asset, "asset_class", ""))
            ).lower()
            tradable = bool(getattr(asset, "tradable", False))

            if status_value != "active" or asset_class_value != "us_equity" or not tradable:
                continue
            if symbol in symbols:
                continue
            symbols.append(symbol)
            if len(symbols) >= limit:
                break

        if not symbols:
            raise RuntimeError("No active tradable US-equity symbols were returned by Alpaca.")
        return symbols

    def get_market_clock(self) -> dict[str, Any]:
        try:
            clock = self.trading_client.get_clock()
        except Exception as exc:
            raise RuntimeError("Alpaca market clock request failed.") from exc

        timestamp = getattr(clock, "timestamp", None)
        next_open = getattr(clock, "next_open", None)
        next_close = getattr(clock, "next_close", None)
        return {
            "is_open": bool(getattr(clock, "is_open", False)),
            "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else None,
            "next_open": next_open.isoformat() if hasattr(next_open, "isoformat") else None,
            "next_close": next_close.isoformat() if hasattr(next_close, "isoformat") else None,
        }


def _parse_timeframe(timeframe: str) -> TimeFrame:
    normalized = timeframe.strip().lower()
    mappings = {
        "1min": TimeFrame(1, TimeFrameUnit.Minute),
        "1minute": TimeFrame(1, TimeFrameUnit.Minute),
        "5min": TimeFrame(5, TimeFrameUnit.Minute),
        "5minute": TimeFrame(5, TimeFrameUnit.Minute),
        "15min": TimeFrame(15, TimeFrameUnit.Minute),
        "15minute": TimeFrame(15, TimeFrameUnit.Minute),
        "1hour": TimeFrame(1, TimeFrameUnit.Hour),
        "1day": TimeFrame(1, TimeFrameUnit.Day),
        "1week": TimeFrame(1, TimeFrameUnit.Week),
        "1month": TimeFrame(1, TimeFrameUnit.Month),
    }
    if normalized in mappings:
        return mappings[normalized]
    raise ValueError(f"Unsupported BAR_TIMEFRAME value: {timeframe}")


def _estimate_lookback_days(limit: int, timeframe: str) -> int:
    normalized = timeframe.strip().lower()
    if normalized in {"1day", "1week", "1month"}:
        return max(60, limit * 7)
    if normalized in {"1hour"}:
        return max(14, (limit // 6) + 7)
    return max(7, (limit // 10) + 3)


def _normalize_trading_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.lower().endswith("/v2"):
        return normalized[:-3]
    return normalized


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_activity_record(activity: Any) -> dict[str, Any] | None:
    if isinstance(activity, dict):
        payload = activity
    else:
        payload = {
            "id": getattr(activity, "id", None),
            "activity_type": getattr(activity, "activity_type", None),
            "transaction_time": getattr(activity, "transaction_time", None),
            "symbol": getattr(activity, "symbol", None),
            "side": getattr(activity, "side", None),
            "qty": getattr(activity, "qty", None),
            "price": getattr(activity, "price", None),
            "net_amount": getattr(activity, "net_amount", None),
            "order_id": getattr(activity, "order_id", None),
            "type": getattr(activity, "type", None),
        }

    symbol = _safe_str(payload.get("symbol")).upper()
    side = _safe_str(payload.get("side")).lower()
    qty = _safe_float(payload.get("qty"))
    price = _safe_float(payload.get("price"))
    net_amount = _safe_float(payload.get("net_amount"))
    value = abs(net_amount) if net_amount != 0.0 else abs(qty * price)
    executed_at = _safe_iso_datetime(payload.get("transaction_time"))

    if not symbol or qty <= 0.0 or not executed_at:
        return None

    return {
        "activity_id": _safe_str(payload.get("id")),
        "activity_type": _safe_str(payload.get("activity_type") or payload.get("type")).upper() or "FILL",
        "order_id": _safe_str(payload.get("order_id")) or None,
        "executed_at": executed_at,
        "symbol": symbol,
        "side": side or "buy",
        "qty": qty,
        "price": price,
        "value": value,
    }


def _safe_iso_datetime(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return ""
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def _extract_activity_page_token(activity: Any) -> str | None:
    if isinstance(activity, dict):
        candidate = activity.get("id") or activity.get("activity_id")
    else:
        candidate = getattr(activity, "id", None)
    token = _safe_str(candidate)
    return token or None


_CACHE_LOCK = Lock()
_CACHE: dict[str, tuple[float, Any]] = {}
_ACCOUNT_OVERVIEW_TTL_SECONDS = 10.0
_OPEN_POSITIONS_TTL_SECONDS = 8.0
_ACCOUNT_ACTIVITIES_TTL_SECONDS = 15.0


def _build_cache_key(client: AlpacaClient, key: str) -> str:
    return f"{client.trading_base_url}|paper={client.paper}|{key}"


def _cache_get(key: str) -> Any | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item is None:
            return None
        expires_at, payload = item
        if now >= expires_at:
            _CACHE.pop(key, None)
            return None
        return payload


def _cache_set(key: str, payload: Any, ttl_seconds: float) -> None:
    expires_at = time.monotonic() + max(0.1, ttl_seconds)
    with _CACHE_LOCK:
        _CACHE[key] = (expires_at, payload)
