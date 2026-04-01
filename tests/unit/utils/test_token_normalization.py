"""Tests for token and trading-pair normalization."""

from unittest.mock import patch

import pytest

import dexalot_sdk.utils.token_normalization as tn


@pytest.fixture(autouse=True)
def clear_token_alias_cache():
    tn._load_token_alias_map.cache_clear()
    yield
    tn._load_token_alias_map.cache_clear()


def test_normalize_token_upper_and_strip():
    assert tn.normalize_token_symbol_for_sdk("  usdc  ") == "USDC"


def test_normalize_token_alias_ether_to_eth():
    assert tn.normalize_token_symbol_for_sdk("ether") == "ETH"


def test_normalize_token_alias_bitcoin():
    assert tn.normalize_token_symbol_for_sdk("BITCOIN") == "BTC"


def test_normalize_pair_mixed_case_and_alias():
    assert tn.normalize_trading_pair_for_sdk(" ether / usdc ") == "ETH/USDC"


def test_normalize_pair_no_slash_upper_only():
    assert tn.normalize_trading_pair_for_sdk("  foo  ") == "FOO"


def test_aliases_rejects_non_object():
    with patch(
        "dexalot_sdk.utils.token_normalization.json.load",
        return_value={"aliases": "not-a-dict"},
    ):
        tn._load_token_alias_map.cache_clear()
        with pytest.raises(ValueError, match="aliases"):
            tn.normalize_token_symbol_for_sdk("ETH")


def test_aliases_skips_non_list_and_non_string_entries():
    raw = {"aliases": {"ETH": ["WETH", 123], 1: ["X"], "OK": "not-a-list", "BTC": ["BITCOIN"]}}
    with patch("dexalot_sdk.utils.token_normalization.json.load", return_value=raw):
        tn._load_token_alias_map.cache_clear()
        assert tn.normalize_token_symbol_for_sdk("WETH") == "ETH"
        assert tn.normalize_token_symbol_for_sdk("BITCOIN") == "BTC"
        assert tn.normalize_token_symbol_for_sdk("OK") == "OK"  # non-list value skipped


def test_aliases_skips_blank_canonical_key():
    raw = {"aliases": {"  ": ["ghost"], "ETH": ["WETH"]}}
    with patch("dexalot_sdk.utils.token_normalization.json.load", return_value=raw):
        tn._load_token_alias_map.cache_clear()
        assert tn.normalize_token_symbol_for_sdk("WETH") == "ETH"
        assert tn.normalize_token_symbol_for_sdk("ghost") == "GHOST"  # no alias mapped


def test_base_client_normalizers_delegate():
    from unittest.mock import mock_open, patch

    from dexalot_sdk.core.base import DexalotBaseClient
    from dexalot_sdk.core.config import DexalotConfig

    with patch("builtins.open", mock_open(read_data='{"E001": "Some Error"}')):
        client = DexalotBaseClient(config=DexalotConfig())
    assert client._normalize_user_token("ether") == "ETH"
    assert client._normalize_user_pair("avax/usdc") == "AVAX/USDC"
