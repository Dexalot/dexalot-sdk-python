"""Input validation utilities for SDK method parameters.

All validators return Result[None] for consistency with SDK error handling.
Validators are lightweight, synchronous functions with early returns.
"""

import math
import re

from .result import Result

# Compiled regex patterns for performance (module-level)
_PAIR_PATTERN = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_-]+$")
_HEX_PATTERN = re.compile(r"^(0x)?[0-9a-fA-F]+$")


def validate_positive_float(value: object, param_name: str) -> Result[None]:
    """Validate that a value is a positive finite float.

    Args:
        value: The value to validate (runtime-checked; may be any type)
        param_name: Name of the parameter (for error messages)

    Returns:
        Result.ok(None) if valid, Result.fail(error_message) if invalid
    """
    if not isinstance(value, (int, float)):
        return Result.fail(
            f"Invalid {param_name}: must be numeric (int or float), got {type(value).__name__}"
        )

    if math.isnan(value):
        return Result.fail(f"Invalid {param_name}: cannot be NaN")

    if math.isinf(value):
        return Result.fail(f"Invalid {param_name}: cannot be infinite")

    if value <= 0:
        return Result.fail(f"Invalid {param_name}: must be positive (> 0), got {value}")

    return Result.ok(None)


def validate_positive_int(value: object, param_name: str) -> Result[None]:
    """Validate that a value is a positive integer.

    Args:
        value: The value to validate (runtime-checked; may be any type)
        param_name: Name of the parameter (for error messages)

    Returns:
        Result.ok(None) if valid, Result.fail(error_message) if invalid
    """
    if not isinstance(value, int):
        return Result.fail(f"Invalid {param_name}: must be integer, got {type(value).__name__}")

    if value <= 0:
        return Result.fail(f"Invalid {param_name}: must be positive (> 0), got {value}")

    return Result.ok(None)


def validate_address(address: object, param_name: str = "address") -> Result[None]:
    """Validate that a string is a valid Ethereum address format.

    Args:
        address: The address string to validate (runtime-checked; may be any type)
        param_name: Name of the parameter (for error messages)

    Returns:
        Result.ok(None) if valid, Result.fail(error_message) if invalid
    """
    if not isinstance(address, str):
        return Result.fail(f"Invalid {param_name}: must be string, got {type(address).__name__}")

    if not address.strip():
        return Result.fail(f"Invalid {param_name}: cannot be empty")

    # Basic Ethereum address format: 0x followed by 40 hex characters
    if not address.startswith("0x"):
        return Result.fail(f"Invalid {param_name}: must start with '0x', got '{address[:10]}...'")

    if len(address) != 42:
        return Result.fail(
            f"Invalid {param_name}: must be 42 characters (0x + 40 hex), got {len(address)} characters"
        )

    # Check hex characters
    hex_part = address[2:]
    if not all(c in "0123456789abcdefABCDEF" for c in hex_part):
        return Result.fail(f"Invalid {param_name}: contains invalid hex characters")

    return Result.ok(None)


def validate_pair_format(pair: object, param_name: str = "pair") -> Result[None]:
    """Validate that a string matches the TOKEN/TOKEN pair format.

    Args:
        pair: The pair string to validate (e.g., "AVAX/USDC"); runtime-checked
        param_name: Name of the parameter (for error messages)

    Returns:
        Result.ok(None) if valid, Result.fail(error_message) if invalid
    """
    if not isinstance(pair, str):
        return Result.fail(f"Invalid {param_name}: must be string, got {type(pair).__name__}")

    if not pair.strip():
        return Result.fail(f"Invalid {param_name}: cannot be empty")

    # Check for exactly one slash first (before regex to provide specific error)
    parts = pair.split("/")
    if len(parts) != 2:
        return Result.fail(
            f"Invalid {param_name}: must contain exactly one '/' separator, got '{pair}'"
        )

    # Check that both tokens are non-empty
    if not parts[0].strip() or not parts[1].strip():
        return Result.fail(f"Invalid {param_name}: both tokens must be non-empty, got '{pair}'")

    # Finally validate format with regex
    if not _PAIR_PATTERN.match(pair):
        return Result.fail(
            f"Invalid {param_name}: must match format TOKEN/TOKEN (e.g., 'AVAX/USDC'), got '{pair}'"
        )

    return Result.ok(None)


def validate_order_id_format(order_id: object, param_name: str = "order_id") -> Result[None]:
    """Validate that an order ID is in a valid format (hex string or bytes32).

    Args:
        order_id: The order ID to validate (hex string with/without 0x, or bytes)
        param_name: Name of the parameter (for error messages)

    Returns:
        Result.ok(None) if valid, Result.fail(error_message) if invalid
    """
    if isinstance(order_id, bytes):
        if len(order_id) != 32:
            return Result.fail(
                f"Invalid {param_name}: bytes must be exactly 32 bytes (bytes32), got {len(order_id)} bytes"
            )
        return Result.ok(None)

    if not isinstance(order_id, str):
        return Result.fail(
            f"Invalid {param_name}: must be string or bytes, got {type(order_id).__name__}"
        )

    if not order_id.strip():
        return Result.fail(f"Invalid {param_name}: cannot be empty")

    # Check hex format
    hex_str = order_id[2:] if order_id.startswith("0x") else order_id
    if not _HEX_PATTERN.match(order_id):
        return Result.fail(
            f"Invalid {param_name}: must be valid hex string (with or without 0x prefix), got '{order_id[:20]}...'"
        )

    # For hex strings, check reasonable length (should be 64 chars for bytes32, but allow flexibility)
    if len(hex_str) > 128:  # Allow up to 64 bytes (512 bits)
        return Result.fail(
            f"Invalid {param_name}: hex string too long (max 128 hex chars), got {len(hex_str)} chars"
        )

    return Result.ok(None)


def validate_token_symbol(token: object, param_name: str = "token") -> Result[None]:
    """Validate that a token symbol is in a valid format.

    Args:
        token: The token symbol to validate (runtime-checked; may be any type)
        param_name: Name of the parameter (for error messages)

    Returns:
        Result.ok(None) if valid, Result.fail(error_message) if invalid
    """
    if not isinstance(token, str):
        return Result.fail(f"Invalid {param_name}: must be string, got {type(token).__name__}")

    if not token.strip():
        return Result.fail(f"Invalid {param_name}: cannot be empty")

    # Token symbols should be alphanumeric with possible special characters
    # Allow common patterns like USDC, AVAX, ALOT, etc.
    if not re.match(r"^[A-Za-z0-9_-]+$", token):
        return Result.fail(
            f"Invalid {param_name}: must contain only alphanumeric characters, underscores, or hyphens, got '{token}'"
        )

    return Result.ok(None)


def validate_chain_identifier(
    chain_identifier: object, param_name: str = "chain_identifier"
) -> Result[None]:
    """Validate that a chain identifier is in a valid format (int or non-empty string).

    Args:
        chain_identifier: Chain ID (int) or chain name (str); runtime-checked
        param_name: Name of the parameter (for error messages)

    Returns:
        Result.ok(None) if valid, Result.fail(error_message) if invalid
    """
    if isinstance(chain_identifier, int):
        if chain_identifier <= 0:
            return Result.fail(
                f"Invalid {param_name}: chain ID must be positive integer, got {chain_identifier}"
            )
        return Result.ok(None)

    if isinstance(chain_identifier, str):
        if not chain_identifier.strip():
            return Result.fail(f"Invalid {param_name}: chain name cannot be empty")
        return Result.ok(None)

    return Result.fail(
        f"Invalid {param_name}: must be int (chain ID) or str (chain name), got {type(chain_identifier).__name__}"
    )


def validate_order_params(
    pair: object, amount: object, price: object | None, order_type: object
) -> Result[None]:
    """Validate order parameters comprehensively.

    Args:
        pair: Trading pair (e.g., "AVAX/USDC")
        amount: Order amount (must be positive)
        price: Order price (must be positive for LIMIT orders, None for MARKET)
        order_type: Order type ("LIMIT" or "MARKET")

    Returns:
        Result.ok(None) if valid, Result.fail(error_message) if invalid
    """
    # Validate pair format
    pair_result = validate_pair_format(pair, "pair")
    if not pair_result.success:
        return pair_result

    # Validate amount
    amount_result = validate_positive_float(amount, "amount")
    if not amount_result.success:
        return amount_result

    # Validate price for LIMIT orders
    order_type_upper = order_type.strip().upper() if isinstance(order_type, str) else ""
    if order_type_upper == "LIMIT":
        if price is None:
            return Result.fail("Invalid price: required for LIMIT orders, got None")
        price_result = validate_positive_float(price, "price")
        if not price_result.success:
            return price_result

    return Result.ok(None)


def validate_transfer_params(token: object, amount: object, to_address: object) -> Result[None]:
    """Validate transfer parameters.

    Args:
        token: Token symbol
        amount: Transfer amount (must be positive)
        to_address: Destination address

    Returns:
        Result.ok(None) if valid, Result.fail(error_message) if invalid
    """
    # Validate token
    token_result = validate_token_symbol(token, "token")
    if not token_result.success:
        return token_result

    # Validate amount
    amount_result = validate_positive_float(amount, "amount")
    if not amount_result.success:
        return amount_result

    # Validate address
    address_result = validate_address(to_address, "to_address")
    if not address_result.success:
        return address_result

    return Result.ok(None)


def validate_swap_params(from_token: object, to_token: object, amount: object) -> Result[None]:
    """Validate swap parameters.

    Args:
        from_token: Source token symbol
        to_token: Destination token symbol
        amount: Swap amount (must be positive)

    Returns:
        Result.ok(None) if valid, Result.fail(error_message) if invalid
    """
    # Validate tokens
    from_token_result = validate_token_symbol(from_token, "from_token")
    if not from_token_result.success:
        return from_token_result

    to_token_result = validate_token_symbol(to_token, "to_token")
    if not to_token_result.success:
        return to_token_result

    # Validate amount
    amount_result = validate_positive_float(amount, "amount")
    if not amount_result.success:
        return amount_result

    return Result.ok(None)
