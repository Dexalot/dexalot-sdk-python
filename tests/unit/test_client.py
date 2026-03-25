import os
from unittest.mock import patch

from dexalot_sdk import DexalotClient


def test_dexalot_client_initialization():
    """Test that DexalotClient initializes and inherits from all base classes."""
    with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
        with patch("dexalot_sdk.core.config.load_dotenv"):
            client = DexalotClient()
            assert hasattr(client, "get_orderbook")  # CLOBClient
            assert hasattr(client, "get_swap_soft_quote")  # SwapClient
            assert hasattr(client, "get_portfolio_balance")  # TransferClient
            assert hasattr(client, "initialize_client")  # BaseClient


def test_unit_conversion():
    """Test the static unit_conversion method."""
    # 1.5 * 10^18
    assert DexalotClient.unit_conversion(1.5, 18, to_base=True) == 1500000000000000000
    # 1500000000000000000 / 10^18
    assert DexalotClient.unit_conversion(1500000000000000000, 18, to_base=False) == 1.5


def test_get_revert_reason():
    """Test the exposed get_revert_reason method."""
    with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
        with patch("dexalot_sdk.core.config.load_dotenv"):
            client = DexalotClient()
            # Mocking the internal _parse_revert_reason or testing its logic indirectly
            # Since it calls the base class method, we can just test basic string parsing if not mocked,
            # or mock the base method.

            # Let's test actual logic if simple, or mock if complex.
            # BaseClient._parse_revert_reason usually handles Exception objects.

            error_msg = Exception("execution reverted: Some Reason")
            reason = client.get_revert_reason(error_msg)
            assert "Some Reason" in reason


def test_configure_logging():
    """Test the static configure_logging method."""
    from unittest.mock import patch

    with patch("dexalot_sdk.core.client.configure_logging") as mock_conf:
        DexalotClient.configure_logging(log_level="DEBUG", log_format="json")
        mock_conf.assert_called_with(log_level="DEBUG", log_format="json")
