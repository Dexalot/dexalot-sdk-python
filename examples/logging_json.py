"""
Example: JSON logging with the Dexalot SDK.

This script demonstrates how to configure JSON logging for production.
Each log entry is output as a single-line JSON object.
"""

import asyncio

from dexalot_sdk import DexalotClient

# Configure JSON logging at INFO level
DexalotClient.configure_logging(log_level="INFO", log_format="json")


async def main():
    # Create client and initialize
    client = DexalotClient()
    try:
        result = await client.initialize_client()

        print(f"\n{result}")
        print("\nJSON logs above can be piped to jq for pretty printing:")
        print("  python examples/logging_json.py 2>&1 | jq .")

        # Make a sample API call to generate logs
        if result.success:
            tokens_result = await client.get_tokens()
            if tokens_result.success:
                print(f"\n✓ Successfully fetched {len(tokens_result.data)} tokens")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
