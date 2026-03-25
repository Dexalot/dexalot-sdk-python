"""
Example: Console logging with the Dexalot SDK.

This script demonstrates how to configure console logging for development.
"""

import asyncio

from dexalot_sdk import DexalotClient

# Configure console logging at INFO level
DexalotClient.configure_logging(log_level="INFO", log_format="console")


async def main():
    # Create client and initialize
    client = DexalotClient()
    try:
        result = await client.initialize_client()

        print(f"\n{result}")
        print("\nCheck the console output above for structured log messages!")

        # Make a sample API call to generate logs
        if result.success:
            tokens_result = await client.get_tokens()
            if tokens_result.success:
                print(f"\n✓ Successfully fetched {len(tokens_result.data)} tokens")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
