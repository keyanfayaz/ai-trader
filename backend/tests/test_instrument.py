from __future__ import annotations

from app.services.instrument import (
    contract_multiplier,
    futures_root,
    is_valid_symbol,
    normalize_asset_class,
    normalize_symbol,
)


def test_normalize_asset_class():
    assert normalize_asset_class(None) == "stock"
    assert normalize_asset_class("FUTURE") == "future"
    assert normalize_asset_class("bogus") == "stock"


def test_normalize_symbol():
    assert normalize_symbol(" es z24 ".replace(" ", "")) == "ESZ24"
    assert normalize_symbol("aapl") == "AAPL"


def test_futures_root():
    assert futures_root("ESZ24") == "ES"
    assert futures_root("MNQH2025") == "MNQ"
    assert futures_root("ES") == "ES"


def test_contract_multiplier():
    assert contract_multiplier("AAPL", "stock") == 1.0
    assert contract_multiplier("ESZ24", "future") == 50.0
    assert contract_multiplier("MNQH25", "future") == 2.0
    assert contract_multiplier("UNKNOWN", "future") == 1.0


def test_is_valid_symbol():
    assert is_valid_symbol("AAPL", "stock")
    assert not is_valid_symbol("", "stock")
    assert is_valid_symbol("ESZ24", "future")
    assert is_valid_symbol("ES", "future")
