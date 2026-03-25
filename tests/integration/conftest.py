import os
import time

import pytest_asyncio

from dexalot_sdk import DexalotClient


@pytest_asyncio.fixture(scope="function")
async def client():
    """Initialize DexalotClient for integration tests."""
    # Ensure we are on Testnet
    os.environ["PARENTENV"] = "fuji-multi"
    client = DexalotClient()
    await client.connect()  # Initialize aiohttp session
    await client.initialize_client()  # Initialize client configuration
    yield client
    # Cleanup: close the session
    if hasattr(client, "_session") and client._session and not client._session.closed:
        await client.close()


async def wait_for_balance_change(client, token, initial_balance, expected_change, timeout=60):
    """Helper to wait for balance update."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        current_balance_result = await client.get_portfolio_balance(token)
        if hasattr(current_balance_result, "success") and current_balance_result.success:
            current_balance = current_balance_result.data
            current_total = current_balance["total"]
            # Check if balance changed in the expected direction
            if expected_change > 0 and current_total > initial_balance:
                return current_total
            if expected_change < 0 and current_total < initial_balance:
                return current_total
        time.sleep(5)
    return None
