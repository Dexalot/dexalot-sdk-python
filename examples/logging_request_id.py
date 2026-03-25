"""
Example: Request ID tracking with the Dexalot SDK.

This script demonstrates how to use request IDs for distributed tracing.
All log entries within a request context will include the same request_id.

Look for the "request_id" field in the JSON log output below.
Each request context (req-001, req-002, req-003) will have its own unique ID
that appears in all log entries within that context.
"""

import asyncio

from dexalot_sdk import DexalotClient
from dexalot_sdk.utils.observability import with_request_id

# Configure JSON logging to see request IDs
DexalotClient.configure_logging(log_level="INFO", log_format="json")


async def main():
    client = None
    try:
        client = DexalotClient()

        # Use request ID context manager
        print("=== Request 1 ===")
        print("Initializing client with request_id='req-001'...")
        with with_request_id("req-001"):
            result = await client.initialize_client()
            if result.success:
                print("✓ Client initialized")
            else:
                print(f"✗ Error: {result.error}")

        print("\n=== Request 2 ===")
        print("Fetching tokens with request_id='req-002'...")
        with with_request_id("req-002"):
            result = await client.get_tokens()
            if result.success:
                tokens = result.data
                print(f"✓ Fetched {len(tokens)} tokens")
            else:
                print(f"✗ Error: {result.error}")

        print("\n=== Request 3 ===")
        print("Fetching environments with request_id='req-003'...")
        with with_request_id("req-003"):
            result = await client.get_environments()
            if result.success:
                envs = result.data
                print(f"✓ Fetched {len(envs)} environments")
            else:
                print(f"✗ Error: {result.error}")

        print("\n" + "=" * 60)
        print("Request ID Tracking Summary:")
        print("  • All log entries above include a 'request_id' field")
        print("  • Request 1 used: request_id='req-001'")
        print("  • Request 2 used: request_id='req-002'")
        print("  • Request 3 used: request_id='req-003'")
        print("\nThis allows you to trace all operations for a specific request")
        print("across distributed systems by filtering logs by request_id.")
        print("=" * 60)
    finally:
        if client is not None:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
