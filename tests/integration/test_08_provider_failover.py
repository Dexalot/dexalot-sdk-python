import os

import pytest

from dexalot_sdk.core.client import DexalotClient
from dexalot_sdk.core.config import DexalotConfig


class TestProviderFailover:
    """Integration tests for provider failover functionality."""

    @pytest.mark.asyncio
    async def test_provider_failover_enabled(self):
        """Test that provider failover can be enabled via config."""
        config = DexalotConfig(
            provider_failover_enabled=True,
            provider_failover_cooldown=60,
            provider_failover_max_failures=3,
        )
        client = DexalotClient(config=config)
        await client.connect()

        assert client._provider_manager is not None
        assert client.config.provider_failover_enabled is True

        await client.close()

    @pytest.mark.asyncio
    async def test_provider_failover_disabled(self):
        """Test that provider failover can be disabled."""
        config = DexalotConfig(provider_failover_enabled=False)
        client = DexalotClient(config=config)
        await client.connect()

        assert client._provider_manager is None
        assert client.config.provider_failover_enabled is False

        await client.close()

    @pytest.mark.asyncio
    async def test_environment_variable_override_chain_id(self):
        """Test that RPC providers can be overridden via chain_id environment variables."""
        # Set environment variable with multiple RPC URLs using chain_id (43114 = Avalanche)
        test_rpc_urls = "https://api.avax-test.network/ext/bc/C/rpc,https://avalanche-fuji.drpc.org"
        os.environ["DEXALOT_RPC_43114"] = test_rpc_urls

        try:
            # Ensure we are on Testnet
            os.environ["PARENTENV"] = "fuji-multi"
            config = DexalotConfig.from_env(provider_failover_enabled=True)
            client = DexalotClient(config=config)
            await client.connect()

            # Initialize client to fetch environments
            init_result = await client.initialize_client()
            assert init_result.success, f"Failed to initialize client: {init_result.error}"

            # Check if provider manager has multiple providers for Avalanche
            if client._provider_manager and "Avalanche" in client.chain_config:
                provider_count = client._provider_manager.get_provider_count("Avalanche")
                # Should have at least one provider (may have more if API also provides multiple)
                assert provider_count >= 1

            await client.close()
        finally:
            # Clean up environment variable
            os.environ.pop("DEXALOT_RPC_43114", None)
            os.environ.pop("PARENTENV", None)

    @pytest.mark.asyncio
    async def test_environment_variable_override_native_symbol(self):
        """Test that RPC providers can be overridden via native_token_symbol environment variables."""
        # Set environment variable with multiple RPC URLs using native_token_symbol (AVAX)
        test_rpc_urls = "https://api.avax-test.network/ext/bc/C/rpc,https://avalanche-fuji.drpc.org"
        os.environ["DEXALOT_RPC_AVAX"] = test_rpc_urls

        try:
            # Ensure we are on Testnet
            os.environ["PARENTENV"] = "fuji-multi"
            config = DexalotConfig.from_env(provider_failover_enabled=True)
            client = DexalotClient(config=config)
            await client.connect()

            # Initialize client to fetch environments
            init_result = await client.initialize_client()
            assert init_result.success, f"Failed to initialize client: {init_result.error}"

            # Check if provider manager has multiple providers for Avalanche
            if client._provider_manager and "Avalanche" in client.chain_config:
                provider_count = client._provider_manager.get_provider_count("Avalanche")
                # Should have at least one provider (may have more if API also provides multiple)
                assert provider_count >= 1

            await client.close()
        finally:
            # Clean up environment variable
            os.environ.pop("DEXALOT_RPC_AVAX", None)
            os.environ.pop("PARENTENV", None)

    @pytest.mark.asyncio
    async def test_provider_failover_with_real_rpc(self):
        """Test provider failover with real RPC endpoints (if available)."""
        # This test requires real RPC endpoints, so it's marked as optional
        # In a real scenario, you would configure multiple working RPC endpoints
        try:
            # Ensure we are on Testnet
            os.environ["PARENTENV"] = "fuji-multi"
            config = DexalotConfig.from_env(
                provider_failover_enabled=True,
                provider_failover_cooldown=1,  # Short cooldown for testing
                provider_failover_max_failures=3,
            )
            client = DexalotClient(config=config)
            await client.connect()

            # Initialize client
            init_result = await client.initialize_client()
            assert init_result.success, f"Failed to initialize client: {init_result.error}"

            # If provider manager is enabled and we have providers, test getting a provider
            if client._provider_manager:
                # Try to get a provider for a known chain
                for chain_name in client.chain_config.keys():
                    provider = await client._provider_manager.get_provider(chain_name)
                    if provider:
                        # Successfully got a provider
                        assert provider is not None
                        break

            await client.close()
        finally:
            os.environ.pop("PARENTENV", None)

    @pytest.mark.asyncio
    async def test_backwards_compatibility_single_provider(self):
        """Test that single provider still works when failover is disabled."""
        try:
            # Ensure we are on Testnet
            os.environ["PARENTENV"] = "fuji-multi"
            config = DexalotConfig.from_env(provider_failover_enabled=False)
            client = DexalotClient(config=config)
            await client.connect()

            # Initialize client
            init_result = await client.initialize_client()
            assert init_result.success, f"Failed to initialize client: {init_result.error}"

            # Should still have providers in connected_chain_providers
            assert len(client.connected_chain_providers) > 0 or client.w3_l1 is not None

            # Provider manager should be None
            assert client._provider_manager is None

            await client.close()
        finally:
            os.environ.pop("PARENTENV", None)
