"""
Dexalot SDK Error Handling Examples

This script demonstrates comprehensive error handling patterns using the Result pattern,
including validation errors, network errors, and best practices.
"""

import asyncio

from dexalot_sdk import DexalotClient
from dexalot_sdk.core.config import DexalotConfig


async def example_validation_errors(client: DexalotClient):
    """Demonstrate input validation error handling."""
    print("=" * 60)
    print("Example 1: Input Validation Errors")
    print("=" * 60)

    print("\n1. Invalid amount (negative):")
    result = await client.add_order(
        pair="AVAX/USDC",
        side="BUY",
        amount=-1.0,  # Invalid: negative amount
        price=25.0,
    )
    if not result.success:
        print(f"   ✓ Validation caught: {result.error}")

    print("\n2. Invalid amount (zero):")
    result = await client.add_order(
        pair="AVAX/USDC",
        side="BUY",
        amount=0.0,  # Invalid: zero amount
        price=25.0,
    )
    if not result.success:
        print(f"   ✓ Validation caught: {result.error}")

    print("\n3. Invalid price (negative):")
    result = await client.add_order(
        pair="AVAX/USDC",
        side="BUY",
        amount=1.0,
        price=-25.0,  # Invalid: negative price
    )
    if not result.success:
        print(f"   ✓ Validation caught: {result.error}")

    print("\n4. Invalid address format:")
    result = await client.get_portfolio_balance(
        token="USDC",
        address="invalid-address",  # Invalid: not a valid Ethereum address
    )
    if not result.success:
        print(f"   ✓ Validation caught: {result.error}")

    print("\n5. Invalid pair format:")
    result = await client.get_orderbook("INVALID_PAIR")  # Invalid: not TOKEN/TOKEN format
    if not result.success:
        print(f"   ✓ Validation caught: {result.error}")


async def example_network_errors(client: DexalotClient):
    """Demonstrate network error handling."""
    print("\n" + "=" * 60)
    print("Example 2: Network Error Handling")
    print("=" * 60)

    print("\n1. Invalid pair (not found):")
    result = await client.get_orderbook("NONEXISTENT/PAIR")
    if not result.success:
        print(f"   ✓ Error handled: {result.error}")

    print("\n2. Order without wallet:")
    # Temporarily remove account to simulate missing wallet
    original_account = client.account
    client.account = None

    result = await client.add_order(pair="AVAX/USDC", side="BUY", amount=1.0, price=25.0)
    if not result.success:
        print(f"   ✓ Error handled: {result.error}")

    # Restore account
    client.account = original_account


async def example_result_pattern_best_practices(client: DexalotClient):
    """Demonstrate Result pattern best practices."""
    print("\n" + "=" * 60)
    print("Example 3: Result Pattern Best Practices")
    print("=" * 60)

    # Best Practice 1: Always check success before accessing data
    print("\n1. Always check result.success:")
    result = await client.get_tokens()

    if result.success:
        tokens = result.data  # Safe to access
        print(f"   ✓ Found {len(tokens)} tokens")
    else:
        print(f"   ✗ Error: {result.error}")
        # Don't access result.data here!

    # Best Practice 2: Use boolean evaluation
    print("\n2. Use boolean evaluation:")
    result = await client.get_clob_pairs()

    if result:  # Equivalent to result.success
        print("   ✓ Pairs fetched successfully")
    else:
        print(f"   ✗ Error: {result.error}")

    # Best Practice 3: Early return pattern
    print("\n3. Early return pattern:")

    async def fetch_orderbook_safely(pair: str):
        result = await client.get_orderbook(pair)
        if not result.success:
            return None  # Early return on error
        return result.data  # Safe to access

    orderbook = await fetch_orderbook_safely("AVAX/USDC")
    if orderbook:
        print(f"   ✓ Orderbook fetched: {len(orderbook.get('bids', []))} bids")
    else:
        print("   ✗ Failed to fetch orderbook")


async def example_error_propagation(client: DexalotClient):
    """Demonstrate error propagation patterns."""
    from dexalot_sdk.utils.result import Result

    print("\n" + "=" * 60)
    print("Example 4: Error Propagation")
    print("=" * 60)

    async def place_order_with_validation(pair: str, side: str, amount: float, price: float):
        """Helper function that validates and places order."""
        # Step 1: Validate pair exists
        pairs_result = await client.get_clob_pairs()
        if not pairs_result.success:
            return Result.fail(f"Failed to fetch pairs: {pairs_result.error}")

        # get_clob_pairs returns Result[str], pairs are stored in client.pairs
        if not hasattr(client, "pairs") or not client.pairs or pair not in client.pairs:
            return Result.fail(f"Pair {pair} not found")

        # Step 2: Place order (validation happens inside add_order)
        return await client.add_order(pair, side, amount, price)

    print("\n1. Successful order placement:")
    result = await place_order_with_validation("AVAX/USDC", "BUY", 1.0, 25.0)
    if result.success:
        print("   ✓ Order placed successfully")
    else:
        print(f"   ✗ Order failed: {result.error}")

    print("\n2. Invalid pair:")
    result = await place_order_with_validation("INVALID/PAIR", "BUY", 1.0, 25.0)
    if not result.success:
        print(f"   ✓ Error propagated: {result.error}")


async def example_retry_pattern(client: DexalotClient):
    """Demonstrate manual retry pattern (SDK has automatic retry, but this shows the pattern)."""
    print("\n" + "=" * 60)
    print("Example 5: Manual Retry Pattern")
    print("=" * 60)

    print("\nNote: SDK has automatic retry with exponential backoff.")
    print("This example shows the pattern if you need custom retry logic:")

    async def fetch_with_retry(operation, max_attempts=3):
        """Manual retry wrapper."""
        for attempt in range(max_attempts):
            result = await operation()
            if result.success:
                return result
            print(f"   Attempt {attempt + 1} failed: {result.error}")
            if attempt < max_attempts - 1:
                await asyncio.sleep(1)  # Wait before retry
        return result  # Return last error

    print("\nFetching with manual retry:")
    result = await fetch_with_retry(lambda: client.get_tokens())
    if result.success:
        print(f"   ✓ Success after retries: {len(result.data)} tokens")
    else:
        print(f"   ✗ Failed after all retries: {result.error}")


async def example_user_friendly_errors(client: DexalotClient):
    """Demonstrate converting SDK errors to user-friendly messages."""
    print("\n" + "=" * 60)
    print("Example 6: User-Friendly Error Messages")
    print("=" * 60)

    def get_user_friendly_error(result):
        """Convert SDK error to user-friendly message."""
        if result.success:
            return None

        error = result.error.lower()

        # Map common errors to user-friendly messages
        if "validation" in error or "invalid" in error:
            return "Please check your input and try again."
        elif "not found" in error:
            return "The requested resource was not found."
        elif "private key" in error or "account" in error:
            return "Please configure your wallet to continue."
        elif "balance" in error or "insufficient" in error:
            return "Insufficient balance for this operation."
        else:
            return "An error occurred. Please try again later."

    print("\n1. Validation error:")
    result = await client.add_order("AVAX/USDC", "BUY", -1.0, 25.0)
    if not result.success:
        user_msg = get_user_friendly_error(result)
        print(f"   SDK Error: {result.error}")
        print(f"   User Message: {user_msg}")

    print("\n2. Not found error:")
    result = await client.get_orderbook("INVALID/PAIR")
    if not result.success:
        user_msg = get_user_friendly_error(result)
        print(f"   SDK Error: {result.error}")
        print(f"   User Message: {user_msg}")


async def main():
    """Run all error handling examples."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 10 + "DEXALOT SDK ERROR HANDLING EXAMPLES" + " " * 16 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    client = None
    try:
        # Initialize client with CRITICAL log level to suppress error logs
        # (The examples demonstrate error handling via Result pattern, not via logs)
        config = DexalotConfig.from_env(log_level="CRITICAL")
        client = DexalotClient(config=config)
        init_result = await client.initialize_client()

        if not init_result.success:
            print(f"✗ Cannot initialize client: {init_result.error}")
            return

        # Run examples
        await example_validation_errors(client)
        await example_network_errors(client)
        await example_result_pattern_best_practices(client)
        await example_error_propagation(client)
        await example_retry_pattern(client)
        await example_user_friendly_errors(client)

        print("\n" + "=" * 60)
        print("All examples completed successfully!")
        print("=" * 60)
        print("\nKey Takeaways:")
        print("  1. Always check result.success before accessing result.data")
        print("  2. Validation errors are caught early with clear messages")
        print("  3. Network errors are automatically retried (configurable)")
        print("  4. Error messages are sanitized for security")
        print("  5. Convert SDK errors to user-friendly messages when needed")
        print()

    except Exception as e:
        print(f"\n❌ Error running examples: {e}")
        print("   Make sure you have a valid .env file with API credentials")
    finally:
        if client is not None:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
