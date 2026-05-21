from __future__ import annotations

import time
from typing import Any

import httpx

from app.clients.providers.base import AssetClass, UnsupportedAssetClassError


_TIMEFRAME_TO_UNIT = {
    "1min": (2, 1),
    "1minute": (2, 1),
    "5min": (2, 5),
    "15min": (2, 15),
    "1hour": (3, 1),
    "1day": (4, 1),
    "1week": (5, 1),
    "1month": (6, 1),
}
_MAX_RETRIES = 4


class TopStepXProvider:
    """Futures-only data + broker provider backed by the ProjectX Gateway API.

    Auth is API-key/OAuth: a session token is fetched from the login endpoint
    and reused. HTTP 429 responses are retried with exponential backoff. The
    HTTP client is injectable so the adapter can be tested without network.
    SignalR streaming is out of scope for v1 (REST polling only).
    """

    provider_id = "topstepx"
    base_url = "https://api.topstepx.com"

    def __init__(
        self,
        api_key: str,
        username: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key or not username:
            raise RuntimeError(
                "TopStepX is selected but TOPSTEPX_API_KEY / TOPSTEPX_USERNAME "
                "are not configured."
            )
        self._api_key = api_key
        self._username = username
        self._http = http_client or httpx.Client(timeout=30.0)
        self._token: str | None = None

    @staticmethod
    def _require_future(asset_class: AssetClass) -> None:
        if asset_class != "future":
            raise UnsupportedAssetClassError(
                "TopStepX supports futures instruments only. Use Alpaca for stocks."
            )

    def _auth_token(self) -> str:
        if self._token:
            return self._token
        try:
            response = self._http.post(
                f"{self.base_url}/api/Auth/loginKey",
                json={"userName": self._username, "apiKey": self._api_key},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("TopStepX authentication failed.") from exc
        token = str((payload or {}).get("token", "")).strip()
        if not token:
            raise RuntimeError("TopStepX authentication returned no token.")
        self._token = token
        return token

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._auth_token()}"
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = self._http.request(
                    method, f"{self.base_url}{path}", headers=headers, **kwargs
                )
                if response.status_code == 429:
                    time.sleep(min(2 ** attempt * 0.5, 8.0))
                    continue
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt + 1 >= _MAX_RETRIES:
                    break
                time.sleep(min(2 ** attempt * 0.5, 8.0))
        raise RuntimeError(f"TopStepX request failed: {method} {path}") from last_exc

    # --- MarketDataProvider ---
    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        asset_class: AssetClass = "future",
    ) -> list[dict[str, Any]]:
        self._require_future(asset_class)
        unit, unit_number = _TIMEFRAME_TO_UNIT.get(timeframe.strip().lower(), (4, 1))
        payload = self._request(
            "POST",
            "/api/History/retrieveBars",
            json={
                "contractId": symbol.upper(),
                "live": False,
                "unit": unit,
                "unitNumber": unit_number,
                "limit": int(limit),
                "includePartialBar": False,
            },
        )
        rows = payload.get("bars", []) if isinstance(payload, dict) else []
        bars: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                bars.append(
                    {
                        "t": row.get("t") or row.get("timestamp"),
                        "o": float(row.get("o", row.get("open", 0.0))),
                        "h": float(row.get("h", row.get("high", 0.0))),
                        "l": float(row.get("l", row.get("low", 0.0))),
                        "c": float(row.get("c", row.get("close", 0.0))),
                        "v": float(row.get("v", row.get("volume", 0.0))),
                    }
                )
            except (TypeError, ValueError):
                continue
        if not bars:
            raise RuntimeError(f"TopStepX returned no bars for '{symbol.upper()}'.")
        return bars[-int(limit):]

    def list_tradable_symbols(
        self,
        limit: int = 200,
        asset_class: AssetClass = "future",
    ) -> list[str]:
        self._require_future(asset_class)
        payload = self._request(
            "POST", "/api/Contract/available", json={"live": False}
        )
        contracts = payload.get("contracts", []) if isinstance(payload, dict) else []
        symbols: list[str] = []
        for contract in contracts:
            if not isinstance(contract, dict):
                continue
            symbol = str(contract.get("id") or contract.get("symbol") or "").upper().strip()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
            if len(symbols) >= int(limit):
                break
        if not symbols:
            raise RuntimeError("TopStepX returned no tradable futures contracts.")
        return symbols

    def get_market_clock(self, asset_class: AssetClass = "future") -> dict[str, Any]:
        # Futures trade nearly 24h; report open and let strategy windows govern.
        return {
            "is_open": True,
            "timestamp": None,
            "next_open": None,
            "next_close": None,
        }

    # --- BrokerProvider ---
    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        time_in_force: str = "day",
        asset_class: AssetClass = "future",
    ) -> dict[str, Any]:
        self._require_future(asset_class)
        side_code = 0 if side.lower() == "buy" else 1
        type_code = {"market": 2, "limit": 1, "stop": 4, "trailing_stop": 5}.get(
            order_type.lower(), 2
        )
        payload = self._request(
            "POST",
            "/api/Order/place",
            json={
                "contractId": symbol.upper(),
                "type": type_code,
                "side": side_code,
                "size": int(qty),
            },
        )
        order_id = str((payload or {}).get("orderId", "")).strip()
        return {
            "id": order_id,
            "symbol": symbol.upper(),
            "qty": float(qty),
            "side": side.lower(),
            "type": order_type.lower(),
            "time_in_force": time_in_force.lower(),
            "status": "accepted" if (payload or {}).get("success") else "rejected",
            "filled_qty": 0.0,
            "filled_avg_price": 0.0,
        }

    def get_order(self, order_id: str) -> dict[str, Any]:
        payload = self._request(
            "POST", "/api/Order/searchById", json={"orderId": order_id}
        )
        order = (payload or {}).get("order", {}) if isinstance(payload, dict) else {}
        return {
            "id": str(order.get("id", order_id)),
            "symbol": str(order.get("contractId", "")),
            "qty": float(order.get("size", 0.0) or 0.0),
            "side": "buy" if int(order.get("side", 0) or 0) == 0 else "sell",
            "type": str(order.get("type", "")),
            "time_in_force": "day",
            "status": str(order.get("status", "")),
            "filled_qty": float(order.get("filledSize", 0.0) or 0.0),
            "filled_avg_price": float(order.get("avgPrice", 0.0) or 0.0),
        }

    def get_account_overview(self) -> dict[str, Any]:
        payload = self._request("POST", "/api/Account/search", json={"onlyActiveAccounts": True})
        accounts = (payload or {}).get("accounts", []) if isinstance(payload, dict) else []
        account = accounts[0] if accounts else {}
        balance = float(account.get("balance", 0.0) or 0.0)
        return {
            "id": str(account.get("id", "")),
            "account_number": str(account.get("name", "")),
            "status": "ACTIVE" if account.get("canTrade") else "RESTRICTED",
            "buying_power": balance,
            "cash": balance,
            "equity": balance,
            "portfolio_value": balance,
            "long_market_value": 0.0,
            "short_market_value": 0.0,
            "last_equity": balance,
            "day_change": 0.0,
            "day_change_pct": 0.0,
        }

    def get_open_positions(self) -> list[dict[str, Any]]:
        payload = self._request("POST", "/api/Position/searchOpen", json={})
        positions = (payload or {}).get("positions", []) if isinstance(payload, dict) else []
        result: list[dict[str, Any]] = []
        for position in positions:
            if not isinstance(position, dict):
                continue
            result.append(
                {
                    "symbol": str(position.get("contractId", "")).upper(),
                    "qty": float(position.get("size", 0.0) or 0.0),
                    "side": "long" if int(position.get("type", 0) or 0) == 0 else "short",
                    "avg_entry_price": float(position.get("averagePrice", 0.0) or 0.0),
                    "market_value": 0.0,
                    "unrealized_pnl": float(position.get("unrealizedPnl", 0.0) or 0.0),
                    "unrealized_pnl_pct": 0.0,
                    "current_price": 0.0,
                    "lastday_price": 0.0,
                    "change_today": 0.0,
                }
            )
        return result

    def get_account_activities(
        self,
        activity_types: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        payload = self._request(
            "POST", "/api/Trade/search", json={"limit": int(limit)}
        )
        trades = (payload or {}).get("trades", []) if isinstance(payload, dict) else []
        normalized: list[dict[str, Any]] = []
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            normalized.append(
                {
                    "activity_id": str(trade.get("id", "")),
                    "activity_type": "FILL",
                    "order_id": str(trade.get("orderId", "")) or None,
                    "executed_at": str(trade.get("creationTimestamp", "")),
                    "symbol": str(trade.get("contractId", "")).upper(),
                    "side": "buy" if int(trade.get("side", 0) or 0) == 0 else "sell",
                    "qty": float(trade.get("size", 0.0) or 0.0),
                    "price": float(trade.get("price", 0.0) or 0.0),
                    "value": float(trade.get("size", 0.0) or 0.0)
                    * float(trade.get("price", 0.0) or 0.0),
                }
            )
        return normalized[: int(limit)]
