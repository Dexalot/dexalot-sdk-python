"""Unit tests for input validation utilities."""

from dexalot_sdk.utils.input_validators import (
    validate_address,
    validate_chain_identifier,
    validate_order_id_format,
    validate_order_params,
    validate_pair_format,
    validate_positive_float,
    validate_positive_int,
    validate_swap_params,
    validate_token_symbol,
    validate_transfer_params,
)
from tests.unit.utils.string_assertions import assert_contains


class TestValidatePositiveFloat:
    """Tests for validate_positive_float."""

    def test_valid_positive_float(self):
        """Test that valid positive floats pass."""
        assert validate_positive_float(1.0, "amount").success
        assert validate_positive_float(0.1, "amount").success
        assert validate_positive_float(100.5, "amount").success
        assert validate_positive_float(1, "amount").success  # int is acceptable

    def test_zero_fails(self):
        """Test that zero fails."""
        result = validate_positive_float(0, "amount")
        assert not result.success
        assert_contains(result.error, 'must be positive')

    def test_negative_fails(self):
        """Test that negative values fail."""
        result = validate_positive_float(-1.0, "amount")
        assert not result.success
        assert_contains(result.error, 'must be positive')

    def test_nan_fails(self):
        """Test that NaN fails."""
        result = validate_positive_float(float("nan"), "amount")
        assert not result.success
        assert_contains(result.error, 'cannot be NaN')

    def test_inf_fails(self):
        """Test that infinity fails."""
        result = validate_positive_float(float("inf"), "amount")
        assert not result.success
        assert_contains(result.error, 'cannot be infinite')

    def test_non_numeric_fails(self):
        """Test that non-numeric types fail."""
        result = validate_positive_float("1.0", "amount")
        assert not result.success
        assert_contains(result.error, 'must be numeric')


class TestValidatePositiveInt:
    """Tests for validate_positive_int."""

    def test_valid_positive_int(self):
        """Test that valid positive integers pass."""
        assert validate_positive_int(1, "chain_id").success
        assert validate_positive_int(100, "chain_id").success

    def test_zero_fails(self):
        """Test that zero fails."""
        result = validate_positive_int(0, "chain_id")
        assert not result.success
        assert_contains(result.error, 'must be positive')

    def test_negative_fails(self):
        """Test that negative values fail."""
        result = validate_positive_int(-1, "chain_id")
        assert not result.success
        assert_contains(result.error, 'must be positive')

    def test_float_fails(self):
        """Test that floats fail."""
        result = validate_positive_int(1.0, "chain_id")
        assert not result.success
        assert_contains(result.error, 'must be integer')


class TestValidateAddress:
    """Tests for validate_address."""

    def test_valid_address(self):
        """Test that valid Ethereum addresses pass."""
        valid_address = "0x1234567890123456789012345678901234567890"
        assert validate_address(valid_address, "address").success

    def test_empty_fails(self):
        """Test that empty strings fail."""
        result = validate_address("", "address")
        assert not result.success
        assert_contains(result.error, 'cannot be empty')

    def test_no_0x_prefix_fails(self):
        """Test that addresses without 0x prefix fail."""
        result = validate_address("1234567890123456789012345678901234567890", "address")
        assert not result.success
        assert_contains(result.error, "must start with '0x'")

    def test_wrong_length_fails(self):
        """Test that addresses with wrong length fail."""
        result = validate_address("0x123", "address")
        assert not result.success
        assert_contains(result.error, 'must be 42 characters')

    def test_invalid_hex_fails(self):
        """Test that addresses with invalid hex characters fail."""
        result = validate_address("0x123456789012345678901234567890123456789g", "address")
        assert not result.success
        assert_contains(result.error, 'invalid hex characters')

    def test_non_string_fails(self):
        """Test that non-string types fail."""
        result = validate_address(123, "address")
        assert not result.success
        assert_contains(result.error, 'must be string')


class TestValidatePairFormat:
    """Tests for validate_pair_format."""

    def test_valid_pair(self):
        """Test that valid pairs pass."""
        assert validate_pair_format("AVAX/USDC", "pair").success
        assert validate_pair_format("ALOT/AVAX", "pair").success
        assert validate_pair_format("TOKEN-1/TOKEN_2", "pair").success

    def test_empty_fails(self):
        """Test that empty strings fail."""
        result = validate_pair_format("", "pair")
        assert not result.success
        assert_contains(result.error, 'cannot be empty')

    def test_no_slash_fails(self):
        """Test that pairs without slash fail."""
        result = validate_pair_format("AVAXUSDC", "pair")
        assert not result.success
        assert_contains(result.error, "must contain exactly one '/'")

    def test_multiple_slashes_fails(self):
        """Test that pairs with multiple slashes fail."""
        result = validate_pair_format("AVAX/USDC/ETH", "pair")
        assert not result.success
        assert_contains(result.error, "must contain exactly one '/'")

    def test_empty_tokens_fail(self):
        """Test that pairs with empty tokens fail."""
        result = validate_pair_format("/USDC", "pair")
        assert not result.success
        assert_contains(result.error, 'both tokens must be non-empty')

    def test_non_string_fails(self):
        """Test that non-string types fail."""
        result = validate_pair_format(123, "pair")
        assert not result.success
        assert_contains(result.error, 'must be string')

    def test_pair_invalid_regex_pattern(self):
        """Test pair that doesn't match regex pattern."""
        # Pair with special characters that don't match the pattern
        result = validate_pair_format("TOKEN@123/TOKEN#456", "pair")
        assert not result.success
        assert_contains(result.error, 'must match format TOKEN/TOKEN')


class TestValidateOrderIdFormat:
    """Tests for validate_order_id_format."""

    def test_valid_hex_string(self):
        """Test that valid hex strings pass."""
        assert validate_order_id_format("0x1234567890abcdef", "order_id").success
        assert validate_order_id_format("1234567890abcdef", "order_id").success

    def test_valid_bytes(self):
        """Test that valid bytes32 pass."""
        valid_bytes = b"\x00" * 32
        assert validate_order_id_format(valid_bytes, "order_id").success

    def test_empty_string_fails(self):
        """Test that empty strings fail."""
        result = validate_order_id_format("", "order_id")
        assert not result.success
        assert_contains(result.error, 'cannot be empty')

    def test_invalid_hex_fails(self):
        """Test that invalid hex strings fail."""
        result = validate_order_id_format("0x123g", "order_id")
        assert not result.success
        assert_contains(result.error, 'must be valid hex string')

    def test_wrong_bytes_length_fails(self):
        """Test that bytes with wrong length fail."""
        result = validate_order_id_format(b"\x00" * 31, "order_id")
        assert not result.success
        assert_contains(result.error, 'must be exactly 32 bytes')

    def test_non_string_or_bytes_fails(self):
        """Test that non-string/non-bytes types fail."""
        result = validate_order_id_format(123, "order_id")
        assert not result.success
        assert_contains(result.error, 'must be string or bytes')

    def test_order_id_hex_string_too_long(self):
        """Test order_id hex string that is too long."""
        # Create a hex string longer than 128 chars (64 bytes)
        long_hex = "0x" + "a" * 130  # 130 hex chars
        result = validate_order_id_format(long_hex, "order_id")
        assert not result.success
        assert_contains(result.error, 'hex string too long')


class TestValidateTokenSymbol:
    """Tests for validate_token_symbol."""

    def test_valid_token_symbols(self):
        """Test that valid token symbols pass."""
        assert validate_token_symbol("AVAX", "token").success
        assert validate_token_symbol("USDC", "token").success
        assert validate_token_symbol("TOKEN-1", "token").success
        assert validate_token_symbol("TOKEN_2", "token").success

    def test_empty_fails(self):
        """Test that empty strings fail."""
        result = validate_token_symbol("", "token")
        assert not result.success
        assert_contains(result.error, 'cannot be empty')

    def test_invalid_characters_fail(self):
        """Test that tokens with invalid characters fail."""
        result = validate_token_symbol("TOKEN@123", "token")
        assert not result.success
        assert_contains(result.error, 'only alphanumeric')

    def test_non_string_fails(self):
        """Test that non-string types fail."""
        result = validate_token_symbol(123, "token")
        assert not result.success
        assert_contains(result.error, 'must be string')


class TestValidateChainIdentifier:
    """Tests for validate_chain_identifier."""

    def test_valid_chain_id(self):
        """Test that valid chain IDs pass."""
        assert validate_chain_identifier(43114, "chain_identifier").success
        assert validate_chain_identifier(1, "chain_identifier").success

    def test_valid_chain_name(self):
        """Test that valid chain names pass."""
        assert validate_chain_identifier("Avalanche", "chain_identifier").success
        assert validate_chain_identifier("Fuji", "chain_identifier").success

    def test_zero_chain_id_fails(self):
        """Test that zero chain ID fails."""
        result = validate_chain_identifier(0, "chain_identifier")
        assert not result.success
        assert_contains(result.error, 'must be positive integer')

    def test_negative_chain_id_fails(self):
        """Test that negative chain ID fails."""
        result = validate_chain_identifier(-1, "chain_identifier")
        assert not result.success
        assert_contains(result.error, 'must be positive integer')

    def test_empty_chain_name_fails(self):
        """Test that empty chain name fails."""
        result = validate_chain_identifier("", "chain_identifier")
        assert not result.success
        assert_contains(result.error, 'cannot be empty')

    def test_invalid_type_fails(self):
        """Test that invalid types fail."""
        result = validate_chain_identifier(1.5, "chain_identifier")
        assert not result.success
        assert_contains(result.error, 'must be int')


class TestValidateOrderParams:
    """Tests for validate_order_params."""

    def test_valid_limit_order(self):
        """Test that valid LIMIT order parameters pass."""
        assert validate_order_params("AVAX/USDC", 1.0, 100.0, "LIMIT").success

    def test_valid_market_order(self):
        """Test that valid MARKET order parameters pass."""
        assert validate_order_params("AVAX/USDC", 1.0, None, "MARKET").success

    def test_invalid_pair_fails(self):
        """Test that invalid pair format fails."""
        result = validate_order_params("INVALID", 1.0, 100.0, "LIMIT")
        assert not result.success
        assert "pair" in result.error.lower()

    def test_invalid_amount_fails(self):
        """Test that invalid amount fails."""
        result = validate_order_params("AVAX/USDC", -1.0, 100.0, "LIMIT")
        assert not result.success
        assert "amount" in result.error.lower()

    def test_missing_price_for_limit_fails(self):
        """Test that missing price for LIMIT order fails."""
        result = validate_order_params("AVAX/USDC", 1.0, None, "LIMIT")
        assert not result.success
        assert_contains(result.error, 'required for LIMIT orders')

    def test_invalid_price_for_limit_fails(self):
        """Test that invalid price for LIMIT order fails."""
        result = validate_order_params("AVAX/USDC", 1.0, -100.0, "LIMIT")
        assert not result.success
        assert "price" in result.error.lower()


class TestValidateTransferParams:
    """Tests for validate_transfer_params."""

    def test_valid_params(self):
        """Test that valid transfer parameters pass."""
        address = "0x1234567890123456789012345678901234567890"
        assert validate_transfer_params("AVAX", 1.0, address).success

    def test_invalid_token_fails(self):
        """Test that invalid token fails."""
        address = "0x1234567890123456789012345678901234567890"
        result = validate_transfer_params("", 1.0, address)
        assert not result.success
        assert "token" in result.error.lower()

    def test_invalid_amount_fails(self):
        """Test that invalid amount fails."""
        address = "0x1234567890123456789012345678901234567890"
        result = validate_transfer_params("AVAX", -1.0, address)
        assert not result.success
        assert "amount" in result.error.lower()

    def test_invalid_address_fails(self):
        """Test that invalid address fails."""
        result = validate_transfer_params("AVAX", 1.0, "invalid")
        assert not result.success
        assert "address" in result.error.lower()


class TestValidateSwapParams:
    """Tests for validate_swap_params."""

    def test_valid_params(self):
        """Test that valid swap parameters pass."""
        assert validate_swap_params("AVAX", "USDC", 1.0).success

    def test_invalid_from_token_fails(self):
        """Test that invalid from_token fails."""
        result = validate_swap_params("", "USDC", 1.0)
        assert not result.success
        assert "from_token" in result.error.lower()

    def test_invalid_to_token_fails(self):
        """Test that invalid to_token fails."""
        result = validate_swap_params("AVAX", "", 1.0)
        assert not result.success
        assert "to_token" in result.error.lower()

    def test_invalid_amount_fails(self):
        """Test that invalid amount fails."""
        result = validate_swap_params("AVAX", "USDC", -1.0)
        assert not result.success
        assert "amount" in result.error.lower()
