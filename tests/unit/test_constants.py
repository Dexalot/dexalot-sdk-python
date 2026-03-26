import pytest

from dexalot_sdk.constants import ws_api_url_for_rest_base


def test_ws_url_https_scheme():
    """https:// base URL produces wss:// WebSocket URL."""
    result = ws_api_url_for_rest_base("https://api.dexalot.com")
    assert result == "wss://api.dexalot.com/api/ws"


def test_ws_url_https_with_trailing_slash():
    """Trailing slash is stripped before building the URL."""
    result = ws_api_url_for_rest_base("https://api.dexalot.com/")
    assert result == "wss://api.dexalot.com/api/ws"


def test_ws_url_http_scheme():
    """http:// base URL produces ws:// WebSocket URL."""
    result = ws_api_url_for_rest_base("http://localhost:8080")
    assert result == "ws://localhost:8080/api/ws"


def test_ws_url_none_defaults_to_mainnet():
    """None input defaults to the mainnet HTTPS URL."""
    result = ws_api_url_for_rest_base(None)
    assert result == "wss://api.dexalot.com/api/ws"


def test_ws_url_unsupported_scheme_raises():
    """Unsupported scheme raises ValueError."""
    with pytest.raises(ValueError, match="Unsupported REST API base URL"):
        ws_api_url_for_rest_base("ftp://api.dexalot.com")
