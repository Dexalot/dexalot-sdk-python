import os
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from dexalot_sdk.core.base import DexalotBaseClient, _load_chain_alias_registry


class TestDexalotBaseClient:
    @pytest.fixture
    def mock_env(self):
        with patch.dict(
            os.environ,
            {
                "PARENTENV": "fuji-multi",
                "API_BASE_URL_TESTNET": "https://api.dexalot-test.com",
                "PRIVATE_KEY": "0x" + "1" * 64,  # 66 chars total (0x + 64 hex chars = 32 bytes)
            },
        ):
            yield

    @pytest.fixture(autouse=True)
    def clean_cache(self):
        from dexalot_sdk.core.base import (
            _BALANCE_CACHE,
            _ORDERBOOK_CACHE,
            _SEMI_STATIC_CACHE,
            _STATIC_CACHE,
        )

        _STATIC_CACHE.clear()
        _SEMI_STATIC_CACHE.clear()
        _BALANCE_CACHE.clear()
        _ORDERBOOK_CACHE.clear()

    @pytest.fixture
    def client(self, mock_env):
        # Mock errors.json loading
        with patch("builtins.open", mock_open(read_data='{"E001": "Some Error"}')):
            with patch("dexalot_sdk.core.base.aiohttp.ClientSession") as mock_session_cls:
                client = DexalotBaseClient()
                # Store the mock on the client to easily access it in tests
                client._mock_session = mock_session_cls.return_value
                # Default mock for get to return a context manager
                mock_cm = AsyncMock()
                client._mock_session.get.return_value = mock_cm

                # Force client to use this session (connect normally creates a new one)
                client._session = client._mock_session

                yield client

    async def test_init(self, client):
        """Test initialization of DexalotBaseClient."""
        assert client.parent_env == "fuji-multi"
        assert client.api_base_url == "https://api.dexalot-test.com"
        assert client.account is not None
        assert (
            client.account.address == "0x19E7E376E7C213B7E7e7e46cc70A5dD086DAff2A"
        )  # Address for key 0x1111...1111 (64 hex chars)
        assert client.error_codes == {"E001": "Some Error"}

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ({}, "top-level 'connected_chains' list"),
            ({"connected_chains": [123]}, "connected chain alias entry must be an object"),
            ({"connected_chains": [{}]}, "non-empty 'connected_chain'"),
            (
                {"connected_chains": [{"connected_chain": "avalanche", "generic_aliases": "bad"}]},
                "list of strings",
            ),
            ({"connected_chains": []}, "top-level 'dexalot_chain' object"),
            (
                {"connected_chains": [], "dexalot_chain": []},
                "top-level 'dexalot_chain' object",
            ),
            (
                {"connected_chains": [], "dexalot_chain": {"generic_aliases": ["dexalot l1"]}},
                "dexalot_chain.canonical_name",
            ),
            (
                {
                    "connected_chains": [],
                    "dexalot_chain": {"canonical_name": "Dexalot L1", "generic_aliases": "bad"},
                },
                "list of strings",
            ),
        ],
    )
    def test_load_chain_alias_registry_validation_errors(self, payload, message):
        _load_chain_alias_registry.cache_clear()
        with patch("builtins.open", mock_open(read_data="{}")):
            with patch("json.load", return_value=payload):
                with pytest.raises(ValueError, match=message):
                    _load_chain_alias_registry()
        _load_chain_alias_registry.cache_clear()

    def test_chain_resolution_helper_branches(self, client):
        registry = {
            "connected_chains": [
                {
                    "connected_chain": "avalanche",
                    "generic_aliases": {"avalanche"},
                    "testnet_aliases": {"fuji"},
                    "mainnet_aliases": {"avax mainnet"},
                }
            ],
            "dexalot_chain": {
                "canonical_name": "Dexalot L1",
                "generic_aliases": {"dexalot chain"},
                "testnet_aliases": {"dexalot testnet"},
                "mainnet_aliases": {"dexalot mainnet"},
            },
        }
        client.chain_config = {
            "Avalanche": {"chain_id": 43114},
            "Fuji": {"chain_id": 43113},
            "Ethereum": {"chain_id": 1},
            "Sepolia": {"chain_id": 11155111},
            "Arbitrum One": {"chain_id": 42161},
            "Arbitrum Sepolia": {"chain_id": 421614},
            "BSC": {"chain_id": 56},
            "BSC Testnet": {"chain_id": 97},
            "Base": {"chain_id": 8453},
            "Base Sepolia": {"chain_id": 84532},
            "Monad Testnet": {"chain_id": 10143},
            "Mystery": {"chain_id": 999999},
        }
        client.subnet_chain_id = 432204

        assert client._infer_chain_family("Ethereum", 1) == "ethereum"
        assert client._infer_chain_family("Arbitrum One", 42161) == "arbitrum"
        assert client._infer_chain_family("BSC", 56) == "bsc"
        assert client._infer_chain_family("Base", 8453) == "base"
        assert client._infer_chain_family("Monad Testnet", 10143) == "monad"
        assert client._infer_chain_family("Mystery", 999999) is None

        assert client._infer_chain_environment_kind("Sepolia", None) == "testnet"
        assert client._infer_chain_environment_kind("Ethereum Mainnet", None) == "mainnet"
        assert client._describe_environment_kind("mainnet") == "mainnet"
        assert client._describe_environment_kind(None) == "current environment"

        with patch("dexalot_sdk.core.base._load_chain_alias_registry", return_value=registry):
            matched = client._match_alias_groups("avax mainnet")
            assert ("avalanche", "mainnet") in matched
            matched = client._match_alias_groups("fuji")
            assert ("avalanche", "testnet") in matched

            candidates = [
                client._get_resolvable_chains()[0],
                client._get_resolvable_chains()[1],
            ]
        client.chain_id = None
        assert client._prefer_active_chain(candidates, "Avalanche") is None
        client.chain_id = 43113
        preferred = client._prefer_active_chain(candidates, "Avalanche")
        assert preferred is not None
        assert preferred.data is not None
        assert preferred.data.canonical_name == "Fuji"

        assert client._mismatch_error("Avalanche", candidates, [("base", None)]) is None

    def test_resolve_chain_reference_edge_cases(self, client):
        registry = {
            "connected_chains": [
                {
                    "connected_chain": "avalanche",
                    "generic_aliases": {"avax"},
                    "testnet_aliases": {"fuji"},
                    "mainnet_aliases": {"avax mainnet"},
                }
            ],
            "dexalot_chain": {
                "canonical_name": "Dexalot L1",
                "generic_aliases": {"dexalot chain"},
                "testnet_aliases": set(),
                "mainnet_aliases": set(),
            },
        }
        client.chain_config = {}
        result = client.resolve_chain_reference("   ")
        assert not result.success
        assert "non-empty" in result.error

        result = client.resolve_chain_reference("Avalanche")
        assert not result.success
        assert "No connected chains" in result.error

        client.chain_config = {"Avalanche": {"chain_id": 43114}, "Fuji": {"chain_id": 43113}}
        client.chain_id = None
        with patch("dexalot_sdk.core.base._load_chain_alias_registry", return_value=registry):
            result = client.resolve_chain_reference("AVAX")
        assert not result.success
        assert "ambiguous" in result.error

    def test_resolve_chain_reference_special_chain(self, client):
        registry = {
            "connected_chains": [],
            "dexalot_chain": {
                "canonical_name": "Dexalot L1",
                "generic_aliases": {"dexalot chain"},
                "testnet_aliases": {"dexalot testnet"},
                "mainnet_aliases": {"dexalot mainnet"},
            },
        }
        client.chain_config = {"Avalanche": {"chain_id": 43114}}
        client.subnet_chain_id = 432204

        with patch("dexalot_sdk.core.base._load_chain_alias_registry", return_value=registry):
            result = client.resolve_chain_reference("Dexalot Chain", include_dexalot_l1=True)

        assert result.success
        assert result.data is not None
        assert result.data.canonical_name == "Dexalot L1"

        with patch("dexalot_sdk.core.base._load_chain_alias_registry", return_value=registry):
            result = client.resolve_chain_reference("Dexalot Testnet", include_dexalot_l1=True)

        assert result.success
        assert result.data is not None
        assert result.data.canonical_name == "Dexalot L1"

    def test_resolve_special_chain_returns_none_when_canonical_chain_missing(self, client):
        registry = {
            "connected_chains": [],
            "dexalot_chain": {
                "canonical_name": "Dexalot L1",
                "generic_aliases": {"dexalot chain"},
                "testnet_aliases": {"dexalot testnet"},
                "mainnet_aliases": {"dexalot mainnet"},
            },
        }
        client.chain_config = {"Avalanche": {"chain_id": 43114}}
        chains = client._get_resolvable_chains(include_dexalot_l1=False)

        with patch("dexalot_sdk.core.base._load_chain_alias_registry", return_value=registry):
            result = client._resolve_special_chain("dexalot chain", chains, "Dexalot Chain")

        assert result is None

    async def test_parse_revert_reason(self, client):
        """Test revert reason parsing."""
        assert client._parse_revert_reason("Execution reverted: E001") == "E001: Some Error"
        assert client._parse_revert_reason("Unknown Error") == "Unknown Error"

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_initialize_client_success(self, mock_web3_base, mock_web3_provider, client):
        """Test successful client initialization."""

        # Mock API responses
        mock_env_resp = [
            {
                "env": "fuji-multi-subnet",
                "chainid": 432204,
                "type": "subnet",
                "rpc": "https://subnet-rpc",
            },
            {
                "env": "fuji-multi-avax",
                "chainid": 43113,
                "type": "mainnet",
                "network": "Fuji",
                "rpc": "https://fuji-rpc",
                "native_token_symbol": "AVAX",
            },
        ]

        mock_tokens_resp = [
            {"symbol": "AVAX", "env": "fuji-multi-subnet", "address": "0x123"},
            {"symbol": "USDC", "env": "fuji-multi-subnet", "address": "0x456"},
        ]

        mock_rfq_resp = {"AVAX/USDC": {}}

        mock_deploy_resp_tp = [{"env": "fuji-multi-subnet", "address": "0xTP", "abi": []}]
        mock_deploy_resp_port = [{"env": "fuji-multi-subnet", "address": "0xPS", "abi": []}]
        mock_deploy_resp_rfq = [{"env": "fuji-multi-avax", "address": "0xRFQ", "abi": []}]

        # Setup side effects for client._session.get
        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = mock_env_resp
            elif "tokens" in url:
                mock_resp.json.return_value = mock_tokens_resp
            elif "rfq/pairs" in url:
                mock_resp.json.return_value = mock_rfq_resp
            elif "deployment" in url:
                ctype = params.get("contracttype")
                if ctype == "TradePairs":
                    mock_resp.json.return_value = mock_deploy_resp_tp
                elif ctype == "Portfolio":
                    mock_resp.json.return_value = mock_deploy_resp_port
                elif ctype == "MainnetRFQ":
                    mock_resp.json.return_value = mock_deploy_resp_rfq

            # Setup __aenter__ for async context manager
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        session = client._mock_session
        session.get.side_effect = side_effect

        # Configure both AsyncWeb3 mocks to return the same mock instance
        mock_web3_instance = MagicMock()
        mock_web3_base.return_value = mock_web3_instance
        mock_web3_provider.return_value = mock_web3_instance

        # Run initialization
        res = await client.initialize_client()

        assert res.success
        assert res.data == "Client initialized with all configurations."
        assert client.subnet_chain_id == 432204
        assert client.env == "fuji-multi-avax"
        assert "Fuji" in client.chain_config
        assert client.token_data["AVAX"]["fuji-multi-subnet"]["address"] == "0x123"
        assert client.deployments["TradePairs"]["address"] == "0xTP"

        # Verify Web3 initialization
        assert client.w3_l1 is not None
        assert client.w3_connected_chain is not None

    async def test_initialize_client_failure(self, client):
        """Test initialization failure handling."""
        client._mock_session.get.side_effect = Exception("API Down")

        res = await client.initialize_client()
        assert not res.success
        assert "initializing client" in res.error.lower()

    def test_transform_environment_from_api(self, client):
        """Test _transform_environment_from_api with various field name combinations."""
        # Test lowercase fields transformed to snake_case
        env1 = {
            "chainid": 43113,
            "type": "mainnet",
            "chain_instance": "https://rpc.example.com",
            "chain_display_name": "Fuji",
            "env": "fuji-multi-avax",
        }
        transformed1 = client._transform_environment_from_api(env1)
        assert transformed1["chain_id"] == 43113
        assert transformed1["env_type"] == "mainnet"
        assert transformed1["rpc"] == "https://rpc.example.com"
        assert transformed1["network"] == "Fuji"

        # Test snake_case fields
        env2 = {
            "chain_id": 12345,
            "type": "subnet",
            "chain_instance": "https://subnet.example.com",
            "chain_display_name": "Dexalot Subnet",
            "env": "fuji-multi-subnet",
        }
        transformed2 = client._transform_environment_from_api(env2)
        assert transformed2["chain_id"] == 12345
        assert transformed2["env_type"] == "subnet"
        assert transformed2["rpc"] == "https://subnet.example.com"
        assert transformed2["network"] == "Dexalot Subnet"

        # Test mixed fields (prefer existing snake_case)
        env3 = {
            "chain_id": 43114,
            "env_type": "mainnet",
            "chainid": 999,  # Should be ignored
            "type": "subnet",  # Should be ignored
            "rpc": "https://rpc.example.com",
            "network": "Avalanche",
        }
        transformed3 = client._transform_environment_from_api(env3)
        assert transformed3["chain_id"] == 43114  # Prefer existing
        assert transformed3["env_type"] == "mainnet"  # Prefer existing

        # Test missing optional fields
        env4 = {"chainid": 43113, "env_type": "mainnet"}
        transformed4 = client._transform_environment_from_api(env4)
        assert transformed4["chain_id"] == 43113
        assert transformed4["env_type"] == "mainnet"
        assert "rpc" not in transformed4 or transformed4.get("rpc") is None
        assert "network" not in transformed4 or transformed4.get("network") is None

    def test_transform_token_from_api(self, client):
        """Test _transform_token_from_api with various field name combinations."""
        # Test lowercase fields transformed to snake_case
        token1 = {
            "symbol": "AVAX",
            "name": "Avalanche",
            "evmdecimals": 18,
            "chainid": 43113,
            "chain_display_name": "Fuji",
            "address": "0x123",
        }
        transformed1 = client._transform_token_from_api(token1)
        assert transformed1["evm_decimals"] == 18
        assert transformed1["chain_id"] == 43113
        assert transformed1["network"] == "Fuji"

        # Test snake_case fields preserved/transformed
        token2 = {
            "symbol": "USDC",
            "name": "USD Coin",
            "decimals": 6,
            "chain_id": 43114,
            "network": "Avalanche",
            "address": "0x456",
        }
        transformed2 = client._transform_token_from_api(token2)
        assert transformed2["evm_decimals"] == 6
        assert transformed2["chain_id"] == 43114
        assert transformed2["network"] == "Avalanche"

        # Test mixed fields (prefer existing snake_case)
        token3 = {
            "symbol": "ETH",
            "evm_decimals": 18,
            "chain_id": 43114,
            "evmdecimals": 999,  # Should be ignored
            "chainid": 999,  # Should be ignored
            "network": "Avalanche",
            "chain_display_name": "Fuji",  # Should be ignored
            "address": "0x789",
        }
        transformed3 = client._transform_token_from_api(token3)
        assert transformed3["evm_decimals"] == 18  # Prefer existing
        assert transformed3["chain_id"] == 43114  # Prefer existing
        assert transformed3["network"] == "Avalanche"  # Prefer existing

        # Test missing optional fields
        token4 = {"symbol": "BTC", "chainid": 43113, "evmdecimals": 8}
        transformed4 = client._transform_token_from_api(token4)
        assert transformed4["evm_decimals"] == 8
        assert transformed4["chain_id"] == 43113
        assert "network" not in transformed4 or transformed4.get("network") is None

    async def test_get_environments(self, client):
        """Test get_environments."""
        mock_resp = AsyncMock()
        mock_resp.json.return_value = [{"env": "test"}]
        mock_resp.raise_for_status = MagicMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_environments()
        assert res.success
        assert res.data == [{"env": "test"}]

    async def test_get_environments_transforms_field_names(self, client):
        """Test get_environments transforms API field names to snake_case."""
        mock_resp = AsyncMock()
        mock_resp.json.return_value = [
            {
                "chainid": 43113,
                "env_type": "mainnet",
                "rpc": "https://rpc.example.com",
                "network": "Fuji",
                "env": "fuji-multi-avax",
            },
            {
                "chain_id": 12345,
                "type": "subnet",
                "chain_instance": "https://subnet.example.com",
                "chain_display_name": "Dexalot Subnet",
                "env": "fuji-multi-subnet",
            },
        ]
        mock_resp.raise_for_status = MagicMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_environments()
        assert res.success
        assert len(res.data) == 2

        # First env: lowercase fields transformed to snake_case
        assert res.data[0]["chain_id"] == 43113
        assert res.data[0]["env_type"] == "mainnet"
        assert res.data[0]["rpc"] == "https://rpc.example.com"
        assert res.data[0]["network"] == "Fuji"

        # Second env: snake_case fields preserved/transformed
        assert res.data[1]["chain_id"] == 12345
        assert res.data[1]["env_type"] == "subnet"
        assert res.data[1]["rpc"] == "https://subnet.example.com"
        assert res.data[1]["network"] == "Dexalot Subnet"

    async def test_get_environments_preserves_existing_snake_case(self, client):
        """Test get_environments prefers existing snake_case fields over transformations."""
        mock_resp = AsyncMock()
        mock_resp.json.return_value = [
            {
                "chain_id": 43114,
                "env_type": "mainnet",
                "chainid": 999,  # Should be ignored
                "type": "subnet",  # Should be ignored
                "rpc": "https://rpc.example.com",
                "network": "Avalanche",
            }
        ]
        mock_resp.raise_for_status = MagicMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_environments()
        assert res.success
        assert res.data[0]["chain_id"] == 43114  # Prefer existing snake_case
        assert res.data[0]["env_type"] == "mainnet"  # Prefer existing snake_case

    async def test_get_tokens_transforms_field_names(self, client):
        """Test get_tokens transforms API field names to snake_case."""
        client.chain_config = {
            "Fuji": {"chain_id": 43113},
            "Avalanche": {"chain_id": 43114},
        }

        mock_resp = AsyncMock()
        mock_resp.json.return_value = [
            {
                "symbol": "AVAX",
                "name": "Avalanche",
                "evmdecimals": 18,
                "chainid": 43113,
                "chain_display_name": "Fuji",
                "address": "0x123",
            },
            {
                "symbol": "USDC",
                "name": "USD Coin",
                "decimals": 6,
                "chain_id": 43114,
                "network": "Avalanche",
                "address": "0x456",
            },
        ]
        mock_resp.raise_for_status = MagicMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_tokens()
        assert res.success
        assert len(res.data) == 2

        # First token: lowercase fields transformed to snake_case
        avax_token = next((t for t in res.data if t["symbol"] == "AVAX"), None)
        assert avax_token is not None
        assert avax_token["decimals"] == 18
        assert avax_token["chain_id"] == 43113
        assert avax_token["chain"] == "Fuji"

        # Second token: snake_case fields preserved/transformed
        usdc_token = next((t for t in res.data if t["symbol"] == "USDC"), None)
        assert usdc_token is not None
        assert usdc_token["decimals"] == 6
        assert usdc_token["chain_id"] == 43114
        assert usdc_token["chain"] == "Avalanche"

    async def test_get_tokens_preserves_existing_snake_case(self, client):
        """Test get_tokens prefers existing snake_case fields over transformations."""
        client.chain_config = {
            "Avalanche": {"chain_id": 43114},
        }

        mock_resp = AsyncMock()
        mock_resp.json.return_value = [
            {
                "symbol": "ETH",
                "evm_decimals": 18,
                "chain_id": 43114,
                "evmdecimals": 999,  # Should be ignored
                "chainid": 999,  # Should be ignored
                "network": "Avalanche",
                "chain_display_name": "Fuji",  # Should be ignored
                "address": "0x789",
            }
        ]
        mock_resp.raise_for_status = MagicMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_tokens()
        assert res.success
        token = next((t for t in res.data if t["symbol"] == "ETH"), None)
        assert token is not None
        assert token["decimals"] == 18  # Prefer existing snake_case
        assert token["chain_id"] == 43114  # Prefer existing snake_case
        assert token["chain"] == "Avalanche"  # Prefer existing network

    async def test_get_chains(self, client):
        """Test get_chains."""
        # Mock environments call (needed since get_chains calls get_environments)
        mock_env_resp = [
            {
                "env": "fuji-multi-avax",
                "chainid": 43113,
                "chain_display_name": "Fuji",
                "rpc": "https://fuji.example.com",
                "type": "mainnet",
            },
            {
                "env": "production-multi-avax",
                "chainid": 43114,
                "chain_display_name": "Avalanche",
                "rpc": "https://avax.example.com",
                "type": "mainnet",
            },
        ]

        mock_resp = AsyncMock()
        mock_resp.json.return_value = mock_env_resp
        mock_resp.raise_for_status = MagicMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_chains()
        assert res.success
        assert res.data == {43114: "Avalanche", 43113: "Fuji"}

    async def test_get_chains_error(self, client):
        """Test get_chains error handling."""
        client.get_environments = AsyncMock(side_effect=Exception("Test error"))
        result = await client.get_chains()
        assert not result.success
        assert "getting chains" in result.error.lower() or "test error" in result.error.lower()

    async def test_get_deployment(self, client):
        """No-args ``get_deployment`` returns the raw REST list and uses defaults."""
        mock_deploy_resp = [
            {"env": "fuji-multi-subnet", "contracttype": "TradePairs", "address": "0xTP"},
            {"env": "fuji-multi-subnet", "contracttype": "PortfolioSub", "address": "0xPS"},
            {"env": "fuji-multi-avax", "contracttype": "MainnetRFQ", "address": "0xRFQ"},
        ]

        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = mock_deploy_resp
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        res = await client.get_deployment()
        assert res.success
        assert res.data == mock_deploy_resp
        # Default filters propagated to query string
        call_kwargs = client._mock_session.get.call_args.kwargs
        assert call_kwargs["params"] == {
            "env": client.parent_env,
            "contracttype": "All",
            "returnabi": "true",
        }

    async def test_get_deployment_error(self, client):
        """REST failures bubble through as ``Result.fail``."""

        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.status = 500
            mock_resp.json.return_value = {}  # empty body — falls through

            def raise_error():
                raise Exception("Test error fetching deployments")

            mock_resp.raise_for_status = raise_error
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.get_deployment()
        assert not result.success
        assert "getting deployment" in result.error.lower() or "test error" in result.error.lower()

    def test_transform_deployment_from_api_prefers_existing_lowercase(self, client):
        """Test _transform_deployment_from_api prefers existing lowercase fields."""
        item = {"env": "fuji-multi-subnet", "address": "0xAddress", "abi": ["function test()"]}
        transformed = client._transform_deployment_from_api(item)
        assert transformed["env"] == "fuji-multi-subnet"
        assert transformed["address"] == "0xAddress"
        assert transformed["abi"] == ["function test()"]

    def test_transform_deployment_from_api_transforms_env(self, client):
        """Test _transform_deployment_from_api transforms Env to env."""
        item = {"Env": "fuji-multi-subnet", "address": "0xAddress", "abi": []}
        transformed = client._transform_deployment_from_api(item)
        assert transformed["env"] == "fuji-multi-subnet"

    def test_transform_deployment_from_api_transforms_environment(self, client):
        """Test _transform_deployment_from_api transforms environment to env."""
        item = {"environment": "fuji-multi-subnet", "address": "0xAddress", "abi": []}
        transformed = client._transform_deployment_from_api(item)
        assert transformed["env"] == "fuji-multi-subnet"

    def test_transform_deployment_from_api_transforms_address(self, client):
        """Test _transform_deployment_from_api transforms Address to address."""
        item = {"env": "fuji-multi-subnet", "Address": "0xAddress", "abi": []}
        transformed = client._transform_deployment_from_api(item)
        assert transformed["address"] == "0xAddress"

    def test_transform_deployment_from_api_transforms_contract_address(self, client):
        """Test _transform_deployment_from_api transforms contractAddress to address."""
        item = {"env": "fuji-multi-subnet", "contractAddress": "0xAddress", "abi": []}
        transformed = client._transform_deployment_from_api(item)
        assert transformed["address"] == "0xAddress"

    def test_transform_deployment_from_api_transforms_abi(self, client):
        """Test _transform_deployment_from_api transforms Abi to abi."""
        item = {"env": "fuji-multi-subnet", "address": "0xAddress", "Abi": ["function test()"]}
        transformed = client._transform_deployment_from_api(item)
        assert transformed["abi"] == ["function test()"]

    def test_transform_deployment_from_api_transforms_abi_uppercase(self, client):
        """Test _transform_deployment_from_api transforms ABI to abi."""
        item = {"env": "fuji-multi-subnet", "address": "0xAddress", "ABI": ["function test()"]}
        transformed = client._transform_deployment_from_api(item)
        assert transformed["abi"] == ["function test()"]

    async def test_process_deployment_item_applies_transformation(self, client):
        """Test _process_deployment_item applies transformation."""
        client.w3_l1 = MagicMock()
        client.w3_l1.eth.contract = MagicMock(return_value=MagicMock())

        # Item with non-standard field names
        item = {
            "Env": "fuji-multi-subnet",
            "Address": "0xTransformed",
            "Abi": {"abi": ["function test()"]},
        }

        # Transform and process
        transformed = client._transform_deployment_from_api(item)
        client._process_deployment_item(transformed, "TradePairs")

        assert "TradePairs" in client.deployments
        assert client.deployments["TradePairs"]["address"] == "0xTransformed"
        assert client.deployments["TradePairs"]["abi"] == ["function test()"]

    async def test_init_production(self):
        """Test initialization with production environment."""
        with patch.dict(
            os.environ,
            {
                "PARENTENV": "production-multi",
                "API_BASE_URL_MAINNET": "https://api.dexalot.com/",  # With trailing slash to test removal
                "PRIVATE_KEY": "",
            },
        ):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                with patch(
                    "builtins.open", side_effect=FileNotFoundError
                ):  # Test missing errors.json
                    # No longer needs to patch requests.Session
                    client = DexalotBaseClient()
                    assert client.api_base_url == "https://api.dexalot.com"
                    assert client.account is None
                    assert client.error_codes == {}

    async def test_init_invalid_key(self):
        """Test initialization with invalid private key."""
        with patch.dict(os.environ, {"PRIVATE_KEY": "invalid_key"}):
            # Invalid key (doesn't start with "0x") should raise ValueError during validation
            with pytest.raises(ValueError, match='private_key must start with "0x"'):
                DexalotBaseClient()

    async def test_init_with_explicit_signer(self):
        """Test initialization with explicit signer parameter (lines 78-79)."""
        mock_account = MagicMock()
        mock_account.address = "0xExplicitSigner"

        with patch.dict(
            os.environ,
            {
                "PRIVATE_KEY": "0x" + "0" * 62 + "01"
            },  # 66 chars total (0x + 64 hex chars = 32 bytes)
        ):
            # Pass explicit signer - should use it instead of PRIVATE_KEY
            client = DexalotBaseClient(signer=mock_account)
            assert client.account == mock_account
            assert client.account.address == "0xExplicitSigner"
            # private_key attribute no longer exists - we only use Account object for signing

    async def test_init_account_from_key_exception(self):
        """Test initialization when Account.from_key() raises an exception (lines 133-134)."""
        with patch.dict(
            os.environ,
            {"PRIVATE_KEY": "0x" + "1" * 64},  # Valid format but will mock to fail
        ):
            with patch("dexalot_sdk.core.base.Account.from_key") as mock_from_key:
                # Mock Account.from_key to raise an exception
                mock_from_key.side_effect = ValueError("Invalid private key format")
                with patch("builtins.open", mock_open(read_data='{"E001": "Some Error"}')):
                    with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
                        client = DexalotBaseClient()
                        # Should handle exception gracefully and set account to None
                        assert client.account is None

    async def test_private_key_zeroed_after_account_creation(self):
        """private_key must be None on config after Account is created."""
        with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "1" * 64}):
            with patch("builtins.open", mock_open(read_data='{"E001": "Some Error"}')):
                with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
                    client = DexalotBaseClient()
                    assert client.account is not None
                    assert client.config.private_key is None

    async def test_private_key_zeroed_on_account_creation_failure(self):
        """private_key must be None on config even when Account.from_key raises."""
        with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "1" * 64}):
            with patch("dexalot_sdk.core.base.Account.from_key") as mock_from_key:
                mock_from_key.side_effect = ValueError("Invalid private key format")
                with patch("builtins.open", mock_open(read_data='{"E001": "Some Error"}')):
                    with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
                        client = DexalotBaseClient()
                        assert client.account is None
                        assert client.config.private_key is None

    async def test_init_with_parent_env_param(self):
        """Test initialization with explicit parent_env parameter."""
        with patch.dict(os.environ, {"PARENTENV": "should-be-overridden"}):
            client = DexalotBaseClient(parent_env="production-multi")
            assert client.parent_env == "production-multi"
            assert client.api_base_url == "https://api.dexalot.com"

    async def test_set_signer(self, client):
        """Test set_signer method (lines 120-121)."""
        # Initially has account from PRIVATE_KEY
        assert client.account is not None

        # Create new mock signer
        new_signer = MagicMock()
        new_signer.address = "0xNewSigner"

        # Set new signer
        client.set_signer(new_signer)

        # Verify signer was updated
        assert client.account == new_signer
        assert client.account.address == "0xNewSigner"
        # private_key attribute no longer exists - we only use Account object for signing

    async def test_repr_with_account(self, client):
        """Test __repr__ method when account is set."""
        # Manually set account using a valid 32-byte private key
        from eth_account import Account

        # Use a valid 32-byte key for Account creation (66 chars total)
        valid_key = "0x" + "1" * 64
        client.account = Account.from_key(valid_key)
        repr_str = repr(client)
        assert "DexalotBaseClient" in repr_str
        assert "parent_env" in repr_str
        assert "api_base_url" in repr_str
        assert "Account(address=" in repr_str
        assert client.account.address in repr_str
        # Ensure private key is never exposed
        assert "private_key" not in repr_str.lower()
        assert valid_key not in repr_str

    async def test_repr_without_account(self, mock_env):
        """Test __repr__ method when account is None."""
        with patch("builtins.open", mock_open(read_data='{"E001": "Some Error"}')):
            with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
                client = DexalotBaseClient()
                client.account = None
                repr_str = repr(client)
                assert "DexalotBaseClient" in repr_str
                assert "parent_env" in repr_str
                assert "api_base_url" in repr_str
                assert "account=None" in repr_str

    async def test_get_environments_failure(self, client):
        """Test get_environments failure."""
        client._mock_session.get.side_effect = Exception("Network Error")
        result = await client.get_environments()
        assert not result.success
        assert "Network Error" in result.error or "getting environments" in result.error

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_initialize_client_production_and_errors(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test initialize_client with production env and error handling."""
        client.parent_env = "production-multi"
        client.env = "production-multi-avax"

        # Mock production environment response
        mock_env_resp = [
            {
                "env": "production-multi-subnet",
                "chainid": 43114,  # Using 43114 for subnet to trigger that branch if logic allows, or just standard prod ID
                "type": "subnet",
                "rpc": "https://subnet-rpc",
            },
            {
                "env": "production-multi-avax",
                "chainid": 43114,
                "type": "mainnet",
                "network": "Avalanche",
                "rpc": "https://avax-rpc",
                "native_token_symbol": "AVAX",
            },
        ]

        # Mock deployment response for PortfolioMain (Avalanche)
        mock_deploy_resp_port_main = [
            {"env": "production-multi-avax", "address": "0xPSMain", "abi": []}
        ]

        call_count_rfq = 0

        def smart_side_effect(url, params=None, **kwargs):
            nonlocal call_count_rfq
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()

            if "environments" in url:
                mock_resp.json.return_value = mock_env_resp
            elif "tokens" in url:
                mock_resp.json.return_value = []
            elif "rfq/pairs" in url:
                # Fail the loop calls
                if call_count_rfq < 1:
                    call_count_rfq += 1
                    raise Exception("RFQ Loop Error")
                # Succeed the fallback call
                mock_resp.json.return_value = {"Fallback": "Data"}
                mock_resp.status = 200
            elif "deployment" in url:
                ctype = params.get("contracttype")
                if ctype == "Portfolio":
                    mock_resp.json.return_value = mock_deploy_resp_port_main
                else:
                    mock_resp.json.return_value = []

            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = smart_side_effect

        # Configure both AsyncWeb3 mocks to return the same mock instance
        mock_web3_instance = MagicMock()
        mock_web3_base.return_value = mock_web3_instance
        mock_web3_provider.return_value = mock_web3_instance

        await client.initialize_client()

        # Verify Production Env settings
        assert client.env == "production-multi-avax"

        assert "Avalanche" in client.deployments["PortfolioMain"]

        # Verify RFQ Fallback
        assert 43114 in client.rfq_pairs
        assert client.rfq_pairs[43114] == {"Fallback": "Data"}

    async def test_fetch_rfq_pairs_status_branches(self, client):
        """200 stores pairs; non-200 statuses and exceptions are silent."""
        client.chain_config = {
            "Avalanche": {"chain_id": 43114},
            "Ethereum": {"chain_id": 1},
            "Broken": {"chain_id": 999},
            "Network": {"chain_id": 12345},
        }

        def make_resp(status, json_data=None):
            resp = AsyncMock()
            resp.status = status
            resp.json.return_value = json_data or {}
            cm = AsyncMock()
            cm.__aenter__.return_value = resp
            return cm

        def side_effect(url, params=None, **kwargs):
            cid = params.get("chainid") if params else None
            if cid == 43114:
                return make_resp(200, {"AVAX/USDC": {}})
            if cid == 1:
                return make_resp(404)
            if cid == 999:
                return make_resp(500)
            raise ConnectionError("network down")

        client._mock_session.get = MagicMock(side_effect=side_effect)
        client.rfq_pairs = {}

        await client._fetch_rfq_pairs()

        assert client.rfq_pairs == {43114: {"AVAX/USDC": {}}}

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_web3_init_failure(self, mock_web3_base, mock_web3_provider, client):
        """Test Web3 provider initialization failure."""
        client.parent_env = "production-multi"
        mock_env_resp = [
            {
                "env": "production-multi-avax",
                "chainid": 43114,
                "type": "mainnet",
                "network": "Avalanche",
                "rpc": "https://avax-rpc",
            }
        ]

        mock_resp = AsyncMock()
        mock_resp.json.return_value = mock_env_resp
        mock_resp.raise_for_status = MagicMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        # Make Web3 raise exception
        mock_web3_base.side_effect = Exception("Web3 Init Failed")
        mock_web3_provider.side_effect = Exception("Web3 Init Failed")

        await client.initialize_client()

        # Check that it didn't crash and logged warning
        assert "Avalanche" in client.chain_config
        assert "Avalanche" not in client.connected_chain_providers

    async def test_coverage_gaps(self, client):
        """Test edge cases in initialization."""

        # Unknown environment
        mock_resp = AsyncMock()
        mock_resp.json.return_value = [{"chainid": 999, "env": "unknown"}]
        mock_resp.raise_for_status = MagicMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        await client.initialize_client()

        # RFQ fallback failure
        def side_effect_rfq(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = []
            elif "rfq/pairs" in url:
                raise Exception("RFQ Fail")
            else:
                mock_resp.json.return_value = []

            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get = MagicMock(side_effect=side_effect_rfq)
        await client.initialize_client()

        # PortfolioMain missing

        client._mock_session.get.side_effect = None

        env_resp = [
            {
                "env": "production-multi-avax",
                "chainid": 43114,
                "type": "mainnet",
                "network": "Avalanche",
                "rpc": "https://rpc",
            }
        ]
        port_resp = [{"env": "production-multi-avax", "address": "0xAddr", "abi": []}]

        def side_effect_port(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = env_resp
            elif "deployment" in url and params.get("contracttype") == "Portfolio":
                mock_resp.json.return_value = port_resp
            else:
                mock_resp.json.return_value = []

            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect_port

        if "PortfolioMain" in client.deployments:
            del client.deployments["PortfolioMain"]

        await client.initialize_client()
        assert "Avalanche" in client.deployments["PortfolioMain"]

    async def test_get_tokens_with_cached_data(self, client):
        """Test get_tokens() with cached token_data."""
        # Setup chain_config with connected-chain IDs
        client.chain_config = {
            "Avalanche": {"chain_id": 43114},
            "Fuji": {"chain_id": 43113},
        }

        # Mock environments call (needed for get_tokens)
        mock_env_resp = [
            {
                "env": "fuji-multi-avax",
                "chainid": 43113,
                "rpc": "https://fuji.example.com",
            }
        ]

        # Mock tokens API response
        mock_tokens_resp = [
            {
                "symbol": "AVAX",
                "name": "Avalanche",
                "chain_id": 43113,
                "evmdecimals": 18,
                "address": "0x0000000000000000000000000000000000000000",
                "chain_display_name": "Fuji",
            },
            {
                "symbol": "USDC",
                "name": "USD Coin",
                "chain_id": 43113,
                "evmdecimals": 6,
                "address": "0x123",
                "chain_display_name": "Fuji",
            },
        ]

        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = mock_env_resp
            elif "tokens" in url:
                mock_resp.json.return_value = mock_tokens_resp

            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.get_tokens()
        assert result.success
        tokens = result.data
        assert isinstance(tokens, list)
        assert len(tokens) == 2
        assert tokens[0]["symbol"] in ["AVAX", "USDC"]
        assert tokens[0]["chain_id"] == 43113

    async def test_get_tokens_with_duplicate_symbols(self, client):
        """Test get_tokens() handles duplicate symbols correctly."""
        client.chain_config = {"Fuji": {"chain_id": 43113}}

        # Mock environments and tokens API
        mock_env_resp = [
            {"env": "fuji-multi-avax", "chainid": 43113, "rpc": "https://fuji.example.com"}
        ]
        mock_tokens_resp = [
            {
                "symbol": "AVAX",
                "name": "Avalanche",
                "chain_id": 43113,
                "evmdecimals": 18,
                "address": "0x0000",
                "chain_display_name": "Fuji",
            }
        ]

        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = mock_env_resp
            elif "tokens" in url:
                mock_resp.json.return_value = mock_tokens_resp
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.get_tokens()
        assert result.success
        tokens = result.data
        assert len(tokens) == 1  # Should only return one even if duplicate

    async def test_get_tokens_duplicate_symbol_skip(self, client):
        """Test get_tokens() skips duplicate symbols."""
        client.chain_config = {"Fuji": {"chain_id": 43113}}

        # Mock environments and tokens API
        mock_env_resp = [
            {"env": "fuji-multi-avax", "chainid": 43113, "rpc": "https://fuji.example.com"}
        ]
        mock_tokens_resp = [
            {
                "symbol": "AVAX",
                "name": "Avalanche",
                "chain_id": 43113,
                "evmdecimals": 18,
                "address": "0x0000",
                "chain_display_name": "Fuji",
            },
            {
                "symbol": "USDC",
                "name": "USD Coin",
                "chain_id": 43113,
                "evmdecimals": 6,
                "address": "0x123",
                "chain_display_name": "Fuji",
            },
        ]

        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = mock_env_resp
            elif "tokens" in url:
                mock_resp.json.return_value = mock_tokens_resp
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.get_tokens()
        assert result.success
        tokens = result.data
        assert len(tokens) == 2
        symbols = [t["symbol"] for t in tokens]
        assert "AVAX" in symbols
        assert "USDC" in symbols
        assert symbols.count("AVAX") == 1

    async def test_get_tokens_fallback_to_api(self, client):
        """Test get_tokens() falls back to API when token_data is not cached."""
        client.chain_config = {"Fuji": {"chain_id": 43113}}
        client.token_data = None  # No cached data

        # Mock environments and tokens API
        mock_env_resp = [
            {"env": "fuji-multi-avax", "chainid": 43113, "rpc": "https://fuji.example.com"}
        ]
        mock_tokens = [
            {
                "symbol": "AVAX",
                "name": "Avalanche",
                "chain_id": 43113,
                "evmdecimals": 18,
                "address": "0x0000",
                "chain_display_name": "Fuji",
            }
        ]

        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = mock_env_resp
            elif "tokens" in url:
                mock_resp.json.return_value = mock_tokens
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.get_tokens()
        assert result.success
        tokens = result.data
        assert isinstance(tokens, list)
        assert len(tokens) == 1
        assert tokens[0]["symbol"] == "AVAX"

    async def test_get_tokens_api_error(self, client):
        """Test get_tokens() handles API errors."""
        client.chain_config = {"Fuji": {"chain_id": 43113}}
        client.token_data = None

        # Mock environments to succeed, tokens to fail
        mock_env_resp = [
            {"env": "fuji-multi-avax", "chainid": 43113, "rpc": "https://fuji.example.com"}
        ]

        def side_effect(url, params=None, **kwargs):
            if "environments" in url:
                mock_resp = AsyncMock()
                mock_resp.json.return_value = mock_env_resp
                mock_resp.raise_for_status = MagicMock()
                mock_cm = AsyncMock()
                mock_cm.__aenter__.return_value = mock_resp
                return mock_cm
            elif "tokens" in url:
                # Raise exception for tokens API
                raise Exception("API Error")

        client._mock_session.get.side_effect = side_effect

        result = await client.get_tokens()
        assert not result.success
        assert "API Error" in result.error or "getting tokens" in result.error

    async def test_get_tokens_filters_by_connected_chain_id(self, client):
        """Test get_tokens() only includes connected-chain tokens."""
        client.chain_config = {"Fuji": {"chain_id": 43113}}

        # Mock environments and tokens API
        mock_env_resp = [
            {"env": "fuji-multi-avax", "chainid": 43113, "rpc": "https://fuji.example.com"}
        ]
        mock_tokens_resp = [
            {
                "symbol": "AVAX",
                "name": "Avalanche",
                "chain_id": 43113,  # Mainnet
                "evmdecimals": 18,
                "address": "0x0000",
                "chain_display_name": "Fuji",
            }
        ]

        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = mock_env_resp
            elif "tokens" in url:
                mock_resp.json.return_value = mock_tokens_resp
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.get_tokens()
        assert result.success
        tokens = result.data
        assert len(tokens) == 1
        assert tokens[0]["chain_id"] == 43113

    async def test_get_tokens_with_chainid_field(self, client):
        """Test get_tokens() handles both chain_id and chainid fields."""
        client.chain_config = {"Fuji": {"chain_id": 43113}}
        client.token_data = None

        # Mock environments and tokens API
        mock_env_resp = [
            {"env": "fuji-multi-avax", "chainid": 43113, "rpc": "https://fuji.example.com"}
        ]
        mock_tokens = [
            {
                "symbol": "AVAX",
                "name": "Avalanche",
                "chainid": 43113,  # Using chainid instead of chain_id
                "evmdecimals": 18,
                "address": "0x0000",
                "chain_display_name": "Fuji",
            }
        ]

        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = mock_env_resp
            elif "tokens" in url:
                mock_resp.json.return_value = mock_tokens
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.get_tokens()
        assert result.success
        tokens = result.data
        assert len(tokens) == 1
        assert tokens[0]["chain_id"] == 43113

    async def test_get_tokens_with_decimals_fallback(self, client):
        """Test get_tokens() uses decimals fallback when evmdecimals missing."""
        client.chain_config = {"Fuji": {"chain_id": 43113}}

        # Mock environments and tokens API
        mock_env_resp = [
            {"env": "fuji-multi-avax", "chainid": 43113, "rpc": "https://fuji.example.com"}
        ]
        mock_tokens_resp = [
            {
                "symbol": "AVAX",
                "name": "Avalanche",
                "chain_id": 43113,
                "decimals": 18,  # Using decimals instead of evmdecimals
                "address": "0x0000",
                "chain_display_name": "Fuji",
            }
        ]

        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = mock_env_resp
            elif "tokens" in url:
                mock_resp.json.return_value = mock_tokens_resp
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.get_tokens()
        assert result.success
        tokens = result.data
        assert tokens[0]["decimals"] == 18

    async def test_get_tokens_with_network_fallback(self, client):
        """Test get_tokens() uses network field when chain_display_name missing."""
        client.chain_config = {"Fuji": {"chain_id": 43113}}

        # Mock environments and tokens API
        mock_env_resp = [
            {"env": "fuji-multi-avax", "chainid": 43113, "rpc": "https://fuji.example.com"}
        ]
        mock_tokens_resp = [
            {
                "symbol": "AVAX",
                "name": "Avalanche",
                "chain_id": 43113,
                "evmdecimals": 18,
                "address": "0x0000",
                "network": "Fuji",  # Using network instead of chain_display_name
            }
        ]

        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = mock_env_resp
            elif "tokens" in url:
                mock_resp.json.return_value = mock_tokens_resp
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.get_tokens()
        assert result.success
        tokens = result.data
        assert tokens[0]["chain"] == "Fuji"

    async def test_context_manager(self, client):
        """Test async context manager methods."""
        async with client as ctx_client:
            assert ctx_client == client
            assert client._session is not None

        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session

        async with client:
            pass

        mock_session.close.assert_called_once()

    async def test_close_with_closed_session(self, client):
        """Test close() when session is already closed."""
        mock_session = AsyncMock()
        mock_session.closed = True
        client._session = mock_session

        await client.close()

        mock_session.close.assert_not_called()

    async def test_close_with_no_session(self, client):
        """Test close() when session is None."""
        client._session = None
        await client.close()

    async def test_close_web3_providers_w3_connected_chain_disconnect_exception(self, client):
        """Test _close_web3_providers when w3_connected_chain.provider.disconnect() raises exception."""
        # Set up w3_connected_chain with a provider that raises exception on disconnect
        mock_provider = AsyncMock()
        mock_provider.disconnect = AsyncMock(side_effect=Exception("Disconnect failed"))
        mock_w3_connected_chain = MagicMock()
        mock_w3_connected_chain.provider = mock_provider
        client.w3_connected_chain = mock_w3_connected_chain

        # Should not raise exception, should handle gracefully
        await client._close_web3_providers()

        # Verify disconnect was called
        mock_provider.disconnect.assert_called_once()
        # Verify w3_connected_chain was set to None
        assert client.w3_connected_chain is None

    async def test_close_web3_providers_connected_chain_providers_disconnect_exception(
        self, client
    ):
        """Test _close_web3_providers when connected_chain_providers disconnect raises exception."""
        # Set up connected_chain_providers with providers that raise exceptions on disconnect
        mock_provider1 = AsyncMock()
        mock_provider1.provider.disconnect = AsyncMock(side_effect=Exception("Disconnect failed 1"))
        mock_provider2 = AsyncMock()
        mock_provider2.provider.disconnect = AsyncMock(side_effect=Exception("Disconnect failed 2"))
        client.connected_chain_providers = {
            "Avalanche": mock_provider1,
            "Fuji": mock_provider2,
        }

        # Should not raise exception, should handle gracefully
        await client._close_web3_providers()

        # Verify disconnect was called for both providers
        mock_provider1.provider.disconnect.assert_called_once()
        mock_provider2.provider.disconnect.assert_called_once()
        # Verify connected_chain_providers was cleared
        assert len(client.connected_chain_providers) == 0

    async def test_rate_limiter_disabled(self, mock_env):
        """Test that rate limiters are None when rate_limit_enabled is False."""
        from dexalot_sdk.core.base import DexalotBaseClient
        from dexalot_sdk.core.config import DexalotConfig

        with patch("builtins.open", mock_open(read_data='{"E001": "Some Error"}')):
            config = DexalotConfig(rate_limit_enabled=False)
            client = DexalotBaseClient(config=config)
            assert client._http_rate_limiter is None
            assert client._rpc_rate_limiter is None

    async def test_make_http_request_retry_disabled(self, client):
        """Test _make_http_request when retry is disabled."""
        client.config.retry_enabled = False
        client._http_rate_limiter = None

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_response
        mock_cm.__aexit__.return_value = None
        client._session.get.return_value = mock_cm

        result = await client._make_http_request("get", "https://test.com")
        assert result == mock_cm
        client._session.get.assert_called_once_with("https://test.com")

    async def test_make_http_request_invalid_method_raises(self, client):
        """Test that an invalid HTTP method raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported HTTP method"):
            await client._make_http_request("close", "https://test.com")

    async def test_rpc_call_retry_disabled(self, client):
        """Test _rpc_call when retry is disabled."""
        from web3 import AsyncWeb3

        client.config.retry_enabled = False
        client._rpc_rate_limiter = None

        mock_w3 = AsyncMock(spec=AsyncWeb3)
        mock_w3.eth = AsyncMock()
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=5)

        result = await client._rpc_call(mock_w3, "eth.get_transaction_count", "0x123", "pending")
        assert result == 5
        mock_w3.eth.get_transaction_count.assert_called_once_with("0x123", "pending")

    async def test_execute_single_rpc_call_rejects_disallowed_method(self, client):
        """Test _execute_single_rpc_call raises ValueError for non-allowlisted method names."""
        from web3 import AsyncWeb3

        mock_w3 = AsyncMock(spec=AsyncWeb3)

        with pytest.raises(ValueError, match="RPC method not allowed"):
            await client._execute_single_rpc_call(mock_w3, "__class__.__mro__")

        with pytest.raises(ValueError, match="RPC method not allowed"):
            await client._execute_single_rpc_call(mock_w3, "provider.close")

    def test_nonce_manager_disabled(self):
        """Test that nonce manager is None when nonce_manager_enabled is False."""
        from dexalot_sdk.core.base import DexalotBaseClient
        from dexalot_sdk.core.config import DexalotConfig

        with patch("builtins.open", mock_open(read_data='{"E001": "Some Error"}')):
            config = DexalotConfig(nonce_manager_enabled=False)
            client = DexalotBaseClient(config=config)
            assert client._nonce_manager is None

    async def test_get_nonce_no_account(self, client):
        """Test _get_nonce raises ValueError when account is None."""
        from web3 import AsyncWeb3

        client.account = None
        mock_w3 = AsyncMock(spec=AsyncWeb3)

        with pytest.raises(ValueError, match="Account is required for nonce management"):
            await client._get_nonce(mock_w3)

    async def test_get_nonce_with_manager_enabled(self, client):
        """Test _get_nonce uses nonce manager when enabled."""
        from web3 import AsyncWeb3

        mock_w3 = MagicMock(spec=AsyncWeb3)
        mock_w3.eth = MagicMock()

        # Mock the nonce manager
        client._nonce_manager = AsyncMock()
        client._nonce_manager.get_nonce = AsyncMock(return_value=5)
        client.account = MagicMock()
        client.account.address = "0x1234567890123456789012345678901234567890"

        result = await client._get_nonce(mock_w3)
        assert result == 5
        client._nonce_manager.get_nonce.assert_called_once_with(
            mock_w3, client.account.address, None
        )

    async def test_get_nonce_with_manager_disabled(self, client):
        """Test _get_nonce falls back to RPC call when nonce manager is disabled."""
        from web3 import AsyncWeb3

        client._nonce_manager = None
        client.account = MagicMock()
        client.account.address = "0x1234567890123456789012345678901234567890"

        mock_w3 = AsyncMock(spec=AsyncWeb3)
        mock_w3.eth = AsyncMock()
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=10)

        result = await client._get_nonce(mock_w3)
        assert result == 10
        mock_w3.eth.get_transaction_count.assert_called_once_with(client.account.address, "pending")

    async def test_get_nonce_with_explicit_chain_id(self, client):
        """Test _get_nonce with explicit chain_id parameter."""
        from web3 import AsyncWeb3

        mock_w3 = MagicMock(spec=AsyncWeb3)
        mock_w3.eth = MagicMock()

        # Mock the nonce manager
        client._nonce_manager = AsyncMock()
        client._nonce_manager.get_nonce = AsyncMock(return_value=7)
        client.account = MagicMock()
        client.account.address = "0x1234567890123456789012345678901234567890"

        result = await client._get_nonce(mock_w3, chain_id=43113)
        assert result == 7
        client._nonce_manager.get_nonce.assert_called_once_with(
            mock_w3, client.account.address, 43113
        )

    async def test_provider_manager_disabled(self, client):
        """Test that provider manager is None when failover is disabled."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(provider_failover_enabled=False)
        client_without_failover = DexalotBaseClient(config=config)
        assert client_without_failover._provider_manager is None

    async def test_find_chain_for_provider_connected_chain_providers(self, client):
        """Test _find_chain_for_provider finding provider in connected_chain_providers."""

        mock_provider = MagicMock()
        client.connected_chain_providers["TestChain"] = mock_provider

        chain_name = client._find_chain_for_provider(mock_provider)
        assert chain_name == "TestChain"

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_rpc_call_with_failover_all_providers_fail(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test _rpc_call_with_failover when all providers fail."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(provider_failover_enabled=True, provider_failover_max_failures=1)
        client_with_failover = DexalotBaseClient(config=config)
        await client_with_failover.connect()

        # Add providers
        await client_with_failover._provider_manager.add_providers(
            "TestChain", ["https://rpc1", "https://rpc2"]
        )

        # Mock providers to fail
        mock_provider1 = MagicMock()
        mock_provider2 = MagicMock()
        client_with_failover._provider_manager._providers["TestChain"] = [
            mock_provider1,
            mock_provider2,
        ]
        client_with_failover._provider_manager._health["TestChain"] = [
            MagicMock(failure_count=0, is_healthy=True, can_retry=lambda *args: True),
            MagicMock(failure_count=0, is_healthy=True, can_retry=lambda *args: True),
        ]

        # Make get_provider return providers that will fail
        async def failing_get_provider(chain_name):
            if chain_name == "TestChain":
                providers = client_with_failover._provider_manager._providers[chain_name]
                current = client_with_failover._provider_manager._current_provider_index.get(
                    chain_name, 0
                )
                if current < len(providers):
                    client_with_failover._provider_manager._current_provider_index[chain_name] = (
                        current + 1
                    )
                    return providers[current]
            return None

        client_with_failover._provider_manager.get_provider = failing_get_provider

        # Mock execute_single_rpc_call to raise exception
        async def failing_rpc_call(*args, **kwargs):
            raise Exception("RPC call failed")

        client_with_failover._execute_single_rpc_call = failing_rpc_call

        # Should raise exception when all providers fail
        with pytest.raises(Exception, match="All RPC providers failed"):
            await client_with_failover._rpc_call_with_failover("TestChain", "eth.gas_price")

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_rpc_call_with_failover_get_provider_returns_none_with_error_inside_loop(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test _rpc_call_with_failover when get_provider returns None with last_error inside loop."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(provider_failover_enabled=True)
        client_with_failover = DexalotBaseClient(config=config)
        await client_with_failover.connect()

        # Add multiple providers to ensure we're in the loop
        await client_with_failover._provider_manager.add_providers(
            "TestChain", ["https://rpc1", "https://rpc2"]
        )

        # Get providers
        mock_provider1 = await client_with_failover._provider_manager.get_provider("TestChain")
        mock_provider1.eth.gas_price = AsyncMock(side_effect=Exception("RPC call failed"))

        # Make get_provider return provider1 first, then None (simulating exhaustion during loop)
        call_count = 0

        async def return_none_after_first(chain_name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_provider1  # First call returns provider (will fail)
            return None  # Second call returns None (inside loop)

        client_with_failover._provider_manager.get_provider = return_none_after_first

        # Should raise exception with last_error (inside loop)
        with pytest.raises(Exception, match="All RPC providers failed") as exc_info:
            await client_with_failover._rpc_call_with_failover("TestChain", "eth.gas_price")
        assert "Last error" in str(exc_info.value)
        # Verify exception chaining (from last_error)
        assert exc_info.value.__cause__ is not None
        assert "RPC call failed" in str(exc_info.value.__cause__)

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_rpc_call_with_failover_get_provider_returns_none_with_error_after_loop(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test _rpc_call_with_failover when get_provider returns None with last_error after loop."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(provider_failover_enabled=True)
        client_with_failover = DexalotBaseClient(config=config)
        await client_with_failover.connect()

        # Add providers
        await client_with_failover._provider_manager.add_providers("TestChain", ["https://rpc1"])

        # Get the provider first
        mock_provider = await client_with_failover._provider_manager.get_provider("TestChain")
        mock_provider.eth.gas_price = AsyncMock(side_effect=Exception("RPC call failed"))

        # Make get_provider return provider first, then None (after loop completes)
        call_count = 0

        async def return_none_after_first(chain_name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_provider  # First call returns provider (will fail)
            return None  # Subsequent calls return None (after loop)

        client_with_failover._provider_manager.get_provider = return_none_after_first

        # Should raise exception with last_error (after loop)
        with pytest.raises(Exception, match="All RPC providers failed") as exc_info:
            await client_with_failover._rpc_call_with_failover("TestChain", "eth.gas_price")
        assert "Last error" in str(exc_info.value)
        # Verify exception chaining (from last_error)
        assert exc_info.value.__cause__ is not None

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_rpc_call_with_failover_provider_index_none(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test _rpc_call_with_failover when get_provider_index returns None."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(provider_failover_enabled=True)
        client_with_failover = DexalotBaseClient(config=config)
        await client_with_failover.connect()

        # Add provider
        await client_with_failover._provider_manager.add_providers("TestChain", ["https://rpc1"])

        mock_provider = await client_with_failover._provider_manager.get_provider("TestChain")
        mock_provider.eth.gas_price = AsyncMock(return_value=1000000000)

        # Make get_provider_index return None
        client_with_failover._provider_manager.get_provider_index = lambda *args: None

        result = await client_with_failover._rpc_call_with_failover("TestChain", "eth.gas_price")
        assert result == 1000000000

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_rpc_call_with_failover_all_providers_exhausted_no_error(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test _rpc_call_with_failover when all providers exhausted with no last_error."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(provider_failover_enabled=True)
        client_with_failover = DexalotBaseClient(config=config)
        await client_with_failover.connect()

        # To test the case where loop completes without raising inside the loop,
        # we need: loop completes AND last_error is None.
        # This can happen if max_providers is 0 (empty loop), or if get_provider_count
        # returns 0 after providers were added but before the loop starts.
        # Let's test with max_providers = 0 by making get_provider_count return 0.

        # Add providers first
        await client_with_failover._provider_manager.add_providers("TestChain", ["https://rpc1"])

        # Make get_provider_count return 0 to create an empty loop
        client_with_failover._provider_manager.get_provider_count = lambda x: 0

        # Should raise exception without last_error (after empty loop, no last_error)
        with pytest.raises(
            Exception, match="All RPC providers failed for chain 'TestChain'"
        ) as exc_info:
            await client_with_failover._rpc_call_with_failover("TestChain", "eth.gas_price")
        assert "Last error" not in str(exc_info.value)
        # Verify no exception chaining (no from clause)
        assert exc_info.value.__cause__ is None
        # Verify it's the exact exception message
        assert str(exc_info.value) == "All RPC providers failed for chain 'TestChain'"

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_rpc_call_with_failover_get_provider_returns_none_no_error_inside_loop(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test _rpc_call_with_failover when get_provider returns None with no last_error inside loop."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(provider_failover_enabled=True)
        client_with_failover = DexalotBaseClient(config=config)
        await client_with_failover.connect()

        # Add providers
        await client_with_failover._provider_manager.add_providers("TestChain", ["https://rpc1"])

        # Make get_provider return None immediately (no providers available, no errors occurred)
        # This will raise on first iteration (inside loop, no last_error)
        async def return_none(*args):
            return None

        client_with_failover._provider_manager.get_provider = return_none

        # Should raise exception without last_error (inside loop, no last_error)
        with pytest.raises(
            Exception, match="All RPC providers failed for chain 'TestChain'"
        ) as exc_info:
            await client_with_failover._rpc_call_with_failover("TestChain", "eth.gas_price")
        assert "Last error" not in str(exc_info.value)
        # Verify no exception chaining (no from clause)
        assert exc_info.value.__cause__ is None
        # Verify it's the exact exception message
        assert str(exc_info.value) == "All RPC providers failed for chain 'TestChain'"

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_process_env_config_provider_init_fails_connected_chain_env(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test _process_environment_config when AsyncWeb3 init fails in fallback."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(provider_failover_enabled=False)
        client_without_failover = DexalotBaseClient(config=config)
        await client_without_failover.connect()

        # Make AsyncWeb3 raise exception
        mock_web3_base.side_effect = Exception("Failed to create provider")

        env = {
            "env_type": "mainnet",
            "chainid": 1,
            "network": "TestChain",
            "rpc": "https://test-rpc",
        }

        # Should not raise, but log warning
        await client_without_failover._process_environment_config(env)
        # Provider should not be in connected_chain_providers
        assert "TestChain" not in client_without_failover.connected_chain_providers

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_process_env_config_provider_init_fails_subnet(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test _process_environment_config when AsyncWeb3 init fails in subnet fallback."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(provider_failover_enabled=True)
        client_with_failover = DexalotBaseClient(config=config)
        await client_with_failover.connect()

        # Make provider manager fail to create providers
        async def failing_add_providers(chain_name, rpc_urls):
            pass  # Don't add any providers

        client_with_failover._provider_manager.add_providers = failing_add_providers

        # Make AsyncWeb3 raise exception in fallback
        mock_web3_base.side_effect = Exception("Failed to create provider")

        env = {
            "env_type": "subnet",
            "rpc": "https://subnet-rpc",
        }

        # Should not raise, but log warning
        await client_with_failover._process_environment_config(env)
        # w3_l1 should be None
        assert client_with_failover.w3_l1 is None

        # Test also the elif branch (provider manager disabled)
        config2 = DexalotConfig(provider_failover_enabled=False)
        client_without_failover = DexalotBaseClient(config=config2)
        await client_without_failover.connect()

        mock_web3_base.side_effect = Exception("Failed to create provider")

        await client_without_failover._process_environment_config(env)
        assert client_without_failover.w3_l1 is None

    async def test_setup_connected_chain_provider_empty_rpc_urls(self, client):
        """Test _setup_connected_chain_provider when rpc_urls is empty."""
        await client.connect()

        # Call with empty rpc_urls - should return early without error
        await client._setup_connected_chain_provider("TestChain", [])

        # Should not add anything to connected_chain_providers
        assert "TestChain" not in client.connected_chain_providers

    async def test_process_subnet_config_empty_rpc_urls(self, client):
        """Test _process_subnet_config when rpc_urls is empty."""
        await client.connect()

        # Call with env that results in empty rpc_urls (no env var, no rpc in env)
        # This happens when _get_rpc_urls returns empty list
        env = {"chainid": 432204, "native_token_symbol": "ALOT"}  # No chain_instance/rpc
        await client._process_subnet_config(env, None)

        # Should not set w3_l1
        assert client.w3_l1 is None

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_rpc_call_with_failover_retry_disabled(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test _rpc_call_with_failover with retry disabled."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(
            provider_failover_enabled=True,
            retry_enabled=False,
            provider_failover_max_failures=3,
        )
        client_with_failover = DexalotBaseClient(config=config)
        await client_with_failover.connect()

        # Add provider
        await client_with_failover._provider_manager.add_providers("TestChain", ["https://rpc1"])

        mock_provider = await client_with_failover._provider_manager.get_provider("TestChain")
        mock_provider.eth.gas_price = AsyncMock(return_value=1000000000)

        result = await client_with_failover._rpc_call_with_failover("TestChain", "eth.gas_price")
        assert result == 1000000000

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_rpc_call_uses_failover(self, mock_web3_base, mock_web3_provider, client):
        """Test _rpc_call using failover when provider manager has providers."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(provider_failover_enabled=True)
        client_with_failover = DexalotBaseClient(config=config)
        await client_with_failover.connect()

        # Add provider and set up chain
        await client_with_failover._provider_manager.add_providers("TestChain", ["https://rpc1"])
        mock_provider = await client_with_failover._provider_manager.get_provider("TestChain")
        client_with_failover.connected_chain_providers["TestChain"] = mock_provider
        client_with_failover.chain_config["TestChain"] = {"chain_id": 1}

        mock_provider.eth.gas_price = AsyncMock(return_value=1000000000)

        # Mock _rpc_call_with_failover to verify it's called
        called_failover = False

        async def mock_failover(*args, **kwargs):
            nonlocal called_failover
            called_failover = True
            return 1000000000

        client_with_failover._rpc_call_with_failover = mock_failover

        result = await client_with_failover._rpc_call(mock_provider, "eth.gas_price")
        assert result == 1000000000
        assert called_failover is True

    async def test_get_rpc_urls_env_var_override_chain_id(self, client):
        """Test _get_rpc_urls with chain_id environment variable override."""
        import os

        os.environ["DEXALOT_RPC_12345"] = "https://rpc1,https://rpc2,https://rpc3"
        try:
            urls = client._get_rpc_urls(12345, None, None)
            assert urls == ["https://rpc1", "https://rpc2", "https://rpc3"]
        finally:
            os.environ.pop("DEXALOT_RPC_12345", None)

    async def test_get_rpc_urls_env_var_override_native_symbol(self, client):
        """Test _get_rpc_urls with native_token_symbol environment variable override."""
        import os

        os.environ["DEXALOT_RPC_TEST"] = "https://rpc1,https://rpc2"
        try:
            urls = client._get_rpc_urls(None, "TEST", None)
            assert urls == ["https://rpc1", "https://rpc2"]
        finally:
            os.environ.pop("DEXALOT_RPC_TEST", None)

    async def test_get_rpc_urls_chain_id_precedence(self, client):
        """Test that chain_id takes precedence over native_token_symbol."""
        import os

        os.environ["DEXALOT_RPC_12345"] = "https://rpc1"
        os.environ["DEXALOT_RPC_TEST"] = "https://rpc2"
        try:
            urls = client._get_rpc_urls(12345, "TEST", None)
            assert urls == ["https://rpc1"]
        finally:
            os.environ.pop("DEXALOT_RPC_12345", None)
            os.environ.pop("DEXALOT_RPC_TEST", None)

    async def test_get_rpc_urls_api_fallback(self, client):
        """Test _get_rpc_urls falling back to API response."""
        api_rpc = "https://api.rpc1,https://api.rpc2"
        urls = client._get_rpc_urls(None, None, api_rpc)
        assert urls == ["https://api.rpc1", "https://api.rpc2"]

    async def test_get_rpc_urls_empty(self, client):
        """Test _get_rpc_urls returning empty list."""
        urls = client._get_rpc_urls(None, None, None)
        assert urls == []

    # --- M-4: TLS / insecure RPC URL tests ---

    async def test_reject_insecure_rpc_urls_raises_on_http_by_default(self, client):
        """http:// URL raises ValueError with default config."""
        with pytest.raises(ValueError, match="http://"):
            client._reject_insecure_rpc_urls(["http://rpc.example.com"])

    async def test_reject_insecure_rpc_urls_passes_https(self, client):
        """https:// URL passes without exception and list is returned unchanged."""
        urls = ["https://rpc.example.com"]
        assert client._reject_insecure_rpc_urls(urls) == urls

    async def test_reject_insecure_rpc_urls_allows_http_when_flag_set(self, client):
        """http:// URL is allowed when allow_insecure_rpc=True."""
        from dexalot_sdk.core.config import DexalotConfig

        client.config = DexalotConfig(allow_insecure_rpc=True)
        urls = ["http://rpc.example.com"]
        assert client._reject_insecure_rpc_urls(urls) == urls

    async def test_reject_insecure_rpc_urls_mixed_raises(self, client):
        """Mixed http/https list raises ValueError naming the insecure URL."""
        with pytest.raises(ValueError, match="http://bad.example.com"):
            client._reject_insecure_rpc_urls(["https://ok.example.com", "http://bad.example.com"])

    async def test_reject_insecure_rpc_urls_empty_list(self, client):
        """Empty list passes silently."""
        assert client._reject_insecure_rpc_urls([]) == []

    async def test_get_rpc_urls_rejects_http_from_env_by_chain_id(self, client):
        """http:// URL from chain-id env var raises ValueError."""
        with patch.dict(os.environ, {"DEXALOT_RPC_99999": "http://insecure.rpc"}):
            with pytest.raises(ValueError, match="http://insecure.rpc"):
                client._get_rpc_urls(99999, None, None)

    async def test_get_rpc_urls_rejects_http_from_env_by_symbol(self, client):
        """http:// URL from native-symbol env var raises ValueError."""
        with patch.dict(os.environ, {"DEXALOT_RPC_ALOT": "http://insecure.rpc"}):
            with pytest.raises(ValueError, match="http://insecure.rpc"):
                client._get_rpc_urls(None, "ALOT", None)

    async def test_get_rpc_urls_rejects_http_from_api_rpc(self, client):
        """http:// URL from API response raises ValueError."""
        with pytest.raises(ValueError, match="http://insecure.rpc"):
            client._get_rpc_urls(None, None, "http://insecure.rpc")

    async def test_get_rpc_urls_allows_http_when_flag_set(self, client):
        """http:// URL is accepted when allow_insecure_rpc=True."""
        from dexalot_sdk.core.config import DexalotConfig

        client.config = DexalotConfig(allow_insecure_rpc=True)
        urls = client._get_rpc_urls(None, None, "http://insecure.rpc")
        assert urls == ["http://insecure.rpc"]

    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_create_provider_fallback_rejects_http_url(self, mock_web3, client):
        """_create_provider_fallback raises ValueError for http:// URLs (not None)."""
        with pytest.raises(ValueError, match="http://"):
            client._create_provider_fallback("http://insecure.rpc", "TestChain")
        mock_web3.assert_not_called()

    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_create_provider_fallback_accepts_https_url(self, mock_web3, client):
        """_create_provider_fallback returns an AsyncWeb3 instance for https:// URLs."""
        mock_instance = MagicMock()
        mock_web3.return_value = mock_instance
        result = client._create_provider_fallback("https://secure.rpc", "TestChain")
        assert result is mock_instance

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_process_env_config_provider_manager_fallback_connected_chain_env(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test _process_environment_config fallback when provider manager fails."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(provider_failover_enabled=True)
        client_with_failover = DexalotBaseClient(config=config)
        await client_with_failover.connect()

        # Make provider manager fail to create providers
        async def failing_add_providers(chain_name, rpc_urls):
            # Don't add any providers
            pass

        client_with_failover._provider_manager.add_providers = failing_add_providers

        env = {
            "env_type": "mainnet",
            "chainid": 1,
            "network": "TestChain",
            "rpc": "https://test-rpc",
        }

        mock_web3_instance = MagicMock()
        mock_web3_base.return_value = mock_web3_instance

        await client_with_failover._process_environment_config(env)

        # Should fallback to direct provider creation
        assert "TestChain" in client_with_failover.connected_chain_providers

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_process_env_config_provider_manager_disabled_connected_chain_env(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test _process_environment_config when provider manager is disabled."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(provider_failover_enabled=False)
        client_without_failover = DexalotBaseClient(config=config)
        await client_without_failover.connect()

        env = {
            "env_type": "mainnet",
            "chainid": 43113,
            "network": "Fuji",
            "rpc": "https://fuji-rpc",
        }

        mock_web3_instance = MagicMock()
        mock_web3_base.return_value = mock_web3_instance

        await client_without_failover._process_environment_config(env)

        # Should set w3_connected_chain from connected_chain_providers
        assert (
            client_without_failover.w3_connected_chain
            == client_without_failover.connected_chain_providers.get("Fuji")
        )

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_process_env_config_provider_manager_fallback_subnet(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test _process_environment_config fallback for subnet when provider manager fails."""
        from dexalot_sdk.core.config import DexalotConfig

        config = DexalotConfig(provider_failover_enabled=True)
        client_with_failover = DexalotBaseClient(config=config)
        await client_with_failover.connect()

        # Make provider manager fail to create providers
        async def failing_add_providers(chain_name, rpc_urls):
            # Don't add any providers
            pass

        client_with_failover._provider_manager.add_providers = failing_add_providers

        env = {
            "env_type": "subnet",
            "rpc": "https://subnet-rpc",
        }

        mock_web3_instance = MagicMock()
        mock_web3_base.return_value = mock_web3_instance

        await client_with_failover._process_environment_config(env)

        # Should fallback to direct provider creation
        assert client_with_failover.w3_l1 is not None

    async def test_sanitize_error_with_parsed_reason(self, client):
        """Test _sanitize_error when parsed_reason != error_str."""
        # Set up error codes so _parse_revert_reason will find a match
        client.error_codes = {"E001": "Some Error Description"}

        # Create an error that contains the error code
        error = Exception("Transaction failed with E001: Some Error Description")

        # Mock sanitize_error_message to verify it's called with parsed_reason
        with patch("dexalot_sdk.core.base.sanitize_error_message") as mock_sanitize:
            mock_sanitize.return_value = "Sanitized error"

            result = client._sanitize_error(error, "test context")

            # Verify sanitize_error_message was called
            assert mock_sanitize.called
            # The parsed reason should be passed (not the original error_str)
            call_args = mock_sanitize.call_args[0]
            assert "E001" in call_args[0] or "Some Error Description" in call_args[0]
            assert result == "Sanitized error"

    @patch("dexalot_sdk.core.base.aiohttp.TCPConnector")
    @patch("dexalot_sdk.core.base.aiohttp.ClientSession")
    async def test_make_http_request_python_lt_314(self, mock_session, mock_connector, client):
        """Test _make_http_request sets enable_cleanup_closed connector kwarg for Python versions < 3.14."""

        # Mock Python version < 3.14
        # Create a version_info-like object that compares as < (3, 14)
        class MockVersionInfo:
            def __init__(self, major, minor):
                self.major = major
                self.minor = minor

            def __lt__(self, other):
                if isinstance(other, tuple):
                    if self.major < other[0]:
                        return True
                    if self.major == other[0] and self.minor < other[1]:
                        return True
                return False

        mock_version = MockVersionInfo(3, 13)
        with patch("dexalot_sdk.core.base.sys.version_info", mock_version):
            # Call connect which uses _make_http_request
            await client.connect()

            # Verify TCPConnector was called with enable_cleanup_closed=True
            assert mock_connector.called
            call_kwargs = mock_connector.call_args[1]
            assert "enable_cleanup_closed" in call_kwargs
            assert call_kwargs["enable_cleanup_closed"] is True

    async def test_fetch_clob_pairs_error_handling(self, client):
        """Test _fetch_clob_pairs error handling when get_clob_pairs returns a failure Result."""

        # Create a mock client with get_clob_pairs method
        class MockCLOBClient(DexalotBaseClient):
            async def get_clob_pairs(self):
                from dexalot_sdk.utils.result import Result

                return Result.fail("CLOB pairs fetch failed")

        mock_client = MockCLOBClient()
        mock_client.api_base_url = "https://api.test.com"
        await mock_client.connect()

        # _fetch_clob_pairs should raise an exception when get_clob_pairs fails
        with pytest.raises(Exception, match="Failed to fetch CLOB pairs: CLOB pairs fetch failed"):
            await mock_client._fetch_clob_pairs()

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_reinitialize_success(self, mock_web3_base, mock_web3_provider, client):
        """Test successful reinitialize."""
        # Mock API responses
        mock_env_resp = [
            {
                "env": "fuji-multi-subnet",
                "chainid": 432204,
                "type": "subnet",
                "rpc": "https://subnet-rpc",
            },
            {
                "env": "fuji-multi-avax",
                "chainid": 43113,
                "type": "mainnet",
                "network": "Fuji",
                "rpc": "https://fuji-rpc",
                "native_token_symbol": "AVAX",
            },
        ]

        mock_tokens_resp = [
            {"symbol": "AVAX", "env": "fuji-multi-subnet", "address": "0x123"},
        ]

        mock_rfq_resp = {"AVAX/USDC": {}}

        mock_deploy_resp_tp = [{"env": "fuji-multi-subnet", "address": "0xTP", "abi": []}]
        mock_deploy_resp_port = [{"env": "fuji-multi-subnet", "address": "0xPS", "abi": []}]
        mock_deploy_resp_rfq = [{"env": "fuji-multi-avax", "address": "0xRFQ", "abi": []}]

        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = mock_env_resp
            elif "tokens" in url:
                mock_resp.json.return_value = mock_tokens_resp
            elif "rfq" in url:
                mock_resp.json.return_value = mock_rfq_resp
            elif "deployment" in url:
                if params and params.get("contracttype") == "TradePairs":
                    mock_resp.json.return_value = mock_deploy_resp_tp
                elif params and params.get("contracttype") == "PortfolioMain":
                    mock_resp.json.return_value = mock_deploy_resp_port
                elif params and params.get("contracttype") == "MainnetRFQ":
                    mock_resp.json.return_value = mock_deploy_resp_rfq
                else:
                    mock_resp.json.return_value = []
            else:
                mock_resp.json.return_value = []
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.reinitialize()
        assert result.success
        assert "reinitialized" in result.data.lower()

    @patch("dexalot_sdk.utils.provider_manager.AsyncWeb3")
    @patch("dexalot_sdk.core.base.AsyncWeb3")
    async def test_reinitialize_with_force_refresh(
        self, mock_web3_base, mock_web3_provider, client
    ):
        """Test reinitialize with force_refresh=True clears caches."""
        from dexalot_sdk.core.base import _SEMI_STATIC_CACHE, _STATIC_CACHE

        # Add some data to caches
        _STATIC_CACHE._store[("test", (), frozenset())] = "test_data"
        _SEMI_STATIC_CACHE._store[("test", (), frozenset())] = "test_data"

        # Mock API responses
        mock_env_resp = [
            {
                "env": "fuji-multi-avax",
                "chainid": 43113,
                "type": "mainnet",
                "network": "Fuji",
                "rpc": "https://fuji-rpc",
                "native_token_symbol": "AVAX",
            },
        ]

        mock_tokens_resp = [{"symbol": "AVAX", "env": "fuji-multi-subnet", "address": "0x123"}]
        mock_rfq_resp = {"AVAX/USDC": {}}
        mock_deploy_resp_tp = [{"env": "fuji-multi-subnet", "address": "0xTP", "abi": []}]
        mock_deploy_resp_port = [{"env": "fuji-multi-subnet", "address": "0xPS", "abi": []}]
        mock_deploy_resp_rfq = [{"env": "fuji-multi-avax", "address": "0xRFQ", "abi": []}]

        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = mock_env_resp
            elif "tokens" in url:
                mock_resp.json.return_value = mock_tokens_resp
            elif "rfq" in url:
                mock_resp.json.return_value = mock_rfq_resp
            elif "deployment" in url:
                if params and params.get("contracttype") == "TradePairs":
                    mock_resp.json.return_value = mock_deploy_resp_tp
                elif params and params.get("contracttype") == "PortfolioMain":
                    mock_resp.json.return_value = mock_deploy_resp_port
                elif params and params.get("contracttype") == "MainnetRFQ":
                    mock_resp.json.return_value = mock_deploy_resp_rfq
                else:
                    mock_resp.json.return_value = []
            else:
                mock_resp.json.return_value = []
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.reinitialize(force_refresh=True)
        assert result.success

        # Verify caches were cleared
        assert len(_STATIC_CACHE._store) == 0
        assert len(_SEMI_STATIC_CACHE._store) == 0

    async def test_reinitialize_error_handling(self, client):
        """Test reinitialize error handling when an exception occurs during reinitialization."""

        # Mock _fetch_environments to raise an exception
        async def failing_fetch():
            raise Exception("Fetch failed")

        client._fetch_environments = failing_fetch

        result = await client.reinitialize()
        assert not result.success
        assert "reinitializing client" in result.error.lower()

    async def test_get_chains_cache_disabled(self, client):
        """Test get_chains with cache disabled."""
        from dexalot_sdk.core.base import _STATIC_CACHE

        # Disable cache
        client._cache_enabled = False

        # Add something to cache
        key = ("get_chains", (client,), frozenset())
        _STATIC_CACHE._store[key] = "cached_data"

        # Mock environments call
        mock_env_resp = [
            {
                "env": "fuji-multi-avax",
                "chainid": 43113,
                "type": "mainnet",
                "network": "Fuji",
                "rpc": "https://fuji-rpc",
                "native_token_symbol": "AVAX",
            },
        ]

        mock_resp = AsyncMock()
        mock_resp.json.return_value = mock_env_resp
        mock_resp.raise_for_status = MagicMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        result = await client.get_chains()

        # Verify cache was cleared
        assert key not in _STATIC_CACHE._store
        assert result.success

    async def test_get_tokens_cache_disabled(self, client):
        """Test get_tokens with cache disabled."""
        from dexalot_sdk.core.base import _SEMI_STATIC_CACHE

        # Disable cache
        client._cache_enabled = False

        # Add something to cache
        key = ("get_tokens", (client,), frozenset())
        _SEMI_STATIC_CACHE._store[key] = "cached_data"

        # Mock API responses
        mock_env_resp = [
            {
                "env": "fuji-multi-avax",
                "chainid": 43113,
                "type": "mainnet",
                "network": "Fuji",
                "rpc": "https://fuji-rpc",
                "native_token_symbol": "AVAX",
            },
        ]

        mock_tokens_resp = [
            {"symbol": "AVAX", "env": "fuji-multi-avax", "address": "0x123", "decimals": 18},
        ]

        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            if "environments" in url:
                mock_resp.json.return_value = mock_env_resp
            elif "tokens" in url:
                mock_resp.json.return_value = mock_tokens_resp
            else:
                mock_resp.json.return_value = []
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.get_tokens()

        # Verify cache was cleared
        assert key not in _SEMI_STATIC_CACHE._store
        assert result.success

    async def test_get_tokens_environments_failure(self, client):
        """Test get_tokens when get_environments fails and chain_config is empty."""
        # Set chain_config to empty
        client.chain_config = {}

        # Mock get_environments to fail
        async def failing_get_environments():
            from dexalot_sdk.utils.result import Result

            return Result.fail("Failed to fetch environments")

        client.get_environments = failing_get_environments

        result = await client.get_tokens()
        assert not result.success
        assert "failed to fetch environments" in result.error.lower()

    async def test_get_deployment_cache_disabled(self, client):
        """``get_deployment`` clears its own cache slot when caching is disabled."""
        from dexalot_sdk.core.base import _STATIC_CACHE

        # Disable cache
        client._cache_enabled = False

        # Pre-seed the cache slot for the no-args call so we can verify it gets cleared.
        # Cache-key shape mirrors the one built inside ``get_deployment``.
        key = (
            "get_deployment",
            client.api_base_url,
            (),
            frozenset(
                {
                    "env": client.parent_env,
                    "contract_type": "All",
                    "return_abi": True,
                }.items()
            ),
        )
        _STATIC_CACHE._store[key] = "cached_data"

        # NB: with _cache_enabled=False, the decorator bypasses the cache
        # entirely (no read, no write). The body of ``get_deployment``
        # still pops the resolved key for defence-in-depth, so the
        # sentinel must disappear.
        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = []
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.get_deployment()

        # Verify the sentinel cache entry was cleared
        assert key not in _STATIC_CACHE._store
        assert result.success

    async def test_rehydrate_cached_get_environments_ignores_failed_or_empty_results(self, client):
        """Cached environment rehydration should skip failed and empty results."""
        from dexalot_sdk.utils.result import Result

        client.chain_config = {"keep": {"chain_id": 1}}

        await client._rehydrate_cached_get_environments(Result.fail("boom"))
        assert client.chain_config == {"keep": {"chain_id": 1}}

        await client._rehydrate_cached_get_environments(Result.ok(None))
        assert client.chain_config == {"keep": {"chain_id": 1}}

    # ------------------------------------------------------------------
    # camelCase transform fallbacks + get_chains env-fail
    # ------------------------------------------------------------------

    def test_transform_environment_camelcase_chain_id_and_env_type(self, client):
        """_transform_environment_from_api falls back to camelCase chainId and envType keys."""
        env = {"chainId": 43114, "envType": "mainnet"}
        result = client._transform_environment_from_api(env)
        assert result["chain_id"] == 43114
        assert result["env_type"] == "mainnet"

    def test_transform_token_camelcase_evm_decimals_and_chain_id(self, client):
        """_transform_token_from_api falls back to camelCase evmDecimals and chainId keys."""
        token = {"symbol": "ETH", "evmDecimals": 18, "chainId": 1}
        result = client._transform_token_from_api(token)
        assert result["evm_decimals"] == 18
        assert result["chain_id"] == 1

    def test_process_deployment_item_trade_pairs_with_w3_l1(self, client):
        """_process_deployment_item creates trade_pairs_contract when w3_l1 is set."""
        client.w3_l1 = MagicMock()
        mock_contract = MagicMock()
        client.w3_l1.eth.contract = MagicMock(return_value=mock_contract)
        client.deployments = {
            "TradePairs": {},
            "PortfolioSub": {},
            "PortfolioMain": {},
            "MainnetRFQ": {},
        }

        item = {
            "env": client.ENV_FUJI_MULTI_SUBNET,
            "address": "0xABCD",
            "abi": [
                {"name": "someFunc"}
            ],  # plain list — exercises the branch where abi_data is used directly (not wrapped in a dict with an "abi" key)
        }
        client._process_deployment_item(item, "TradePairs")

        assert client.deployments["TradePairs"]["address"] == "0xABCD"
        client.w3_l1.eth.contract.assert_called_once_with(
            address="0xABCD", abi=[{"name": "someFunc"}]
        )
        assert client.trade_pairs_contract is mock_contract

    async def test_get_chains_env_fail_propagates(self, client):
        """get_chains returns Result.fail when get_environments fails."""
        with patch.object(
            client,
            "get_environments",
            new=AsyncMock(return_value=MagicMock(success=False, error="env error")),
        ):
            result = await client.get_chains()
        assert not result.success
        assert "env error" in result.error

    async def test_connect_python314_connector_has_no_enable_cleanup_closed(self, client, mock_env):
        """On Python >= 3.14 the TCPConnector is created without enable_cleanup_closed."""
        client._session = None  # force a new session to be created

        with patch("dexalot_sdk.core.base.sys") as mock_sys:
            mock_sys.version_info = (3, 14, 0)
            with patch("dexalot_sdk.core.base.aiohttp.TCPConnector") as mock_connector:
                with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
                    await client.connect()

        # enable_cleanup_closed must NOT appear in the kwargs for the 3.14+ branch
        call_kwargs = mock_connector.call_args.kwargs
        assert "enable_cleanup_closed" not in call_kwargs

    async def test_rpc_call_with_failover_no_provider_manager_raises(self, client):
        """_rpc_call_with_failover raises RuntimeError when _provider_manager is None."""
        client._provider_manager = None
        with pytest.raises(RuntimeError, match="Provider manager is required"):
            await client._rpc_call_with_failover("Avalanche", "eth.get_block", "latest")

    # -------------------------------------------------------------------- #
    # _api_call — REST error body preservation                             #
    # -------------------------------------------------------------------- #
    # The Dexalot REST API encodes failures as
    # ``{"reasonCode": "...", "reason": "..."}`` (also tolerates the
    # snake_case ``reason_code`` and the ``message`` alias that some
    # endpoints emit). ``_api_call`` must lift these into the raised
    # exception so callers' ``Result.fail`` strings surface the backend
    # reason instead of collapsing to ``"... HTTP status N"``.

    @staticmethod
    def _http_error_response(status: int, body):
        """Build a context-manager mock that yields a response with the given body."""

        async def _json(content_type=None):
            if isinstance(body, Exception):
                raise body
            return body

        mock_resp = AsyncMock()
        mock_resp.status = status
        mock_resp.json = _json

        if status >= 400:
            import aiohttp

            def _raise():
                raise aiohttp.ClientResponseError(
                    request_info=MagicMock(),
                    history=(),
                    status=status,
                    message=f"Request failed with status code {status}",
                )

            mock_resp.raise_for_status = MagicMock(side_effect=_raise)
        else:
            mock_resp.raise_for_status = MagicMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        mock_cm.__aexit__.return_value = None
        return mock_cm

    async def test_api_call_lifts_reason_code_and_reason(self, client):
        """_api_call surfaces backend reasonCode + reason from response body."""
        cm = self._http_error_response(
            400, {"reasonCode": "FQ-015", "reason": "insufficient liquidity"}
        )
        with patch.object(client, "_make_http_request", AsyncMock(return_value=cm)):
            with pytest.raises(RuntimeError, match=r"^FQ-015: insufficient liquidity$"):
                await client._api_call("get", "https://api/x")

    async def test_api_call_lifts_snake_case_aliases(self, client):
        """``reason_code`` + ``message`` aliases are also lifted."""
        cm = self._http_error_response(
            400, {"reason_code": "T-TMDQ-01", "message": "amount too small"}
        )
        with patch.object(client, "_make_http_request", AsyncMock(return_value=cm)):
            with pytest.raises(RuntimeError, match=r"^T-TMDQ-01: amount too small$"):
                await client._api_call("get", "https://api/x")

    async def test_api_call_reason_code_without_reason_uses_generic_tail(self, client):
        """When reasonCode is set but reason is missing, fall back to a generic tail."""
        cm = self._http_error_response(500, {"reasonCode": "P-OK01"})
        with patch.object(client, "_make_http_request", AsyncMock(return_value=cm)):
            with pytest.raises(RuntimeError, match=r"^P-OK01: Request failed with status code 500$"):
                await client._api_call("get", "https://api/x")

    async def test_api_call_reason_alone_without_reason_code(self, client):
        """Reason alone (no reasonCode) is used as the full message."""
        cm = self._http_error_response(400, {"reason": "something else"})
        with patch.object(client, "_make_http_request", AsyncMock(return_value=cm)):
            with pytest.raises(RuntimeError, match=r"^something else$"):
                await client._api_call("get", "https://api/x")

    async def test_api_call_message_alias_alone(self, client):
        """``message`` alias alone (no reasonCode) is used as the full message."""
        cm = self._http_error_response(400, {"message": "plain reason from message field"})
        with patch.object(client, "_make_http_request", AsyncMock(return_value=cm)):
            with pytest.raises(RuntimeError, match=r"^plain reason from message field$"):
                await client._api_call("get", "https://api/x")

    async def test_api_call_empty_body_falls_through_to_aiohttp_error(self, client):
        """Empty ``{}`` body falls through to the generic aiohttp error."""
        import aiohttp

        cm = self._http_error_response(500, {})
        with patch.object(client, "_make_http_request", AsyncMock(return_value=cm)):
            with pytest.raises(aiohttp.ClientResponseError):
                await client._api_call("get", "https://api/x")

    async def test_api_call_non_dict_body_falls_through(self, client):
        """HTML / non-JSON-object body falls through to the generic aiohttp error."""
        import aiohttp

        cm = self._http_error_response(502, "<html>502 Bad Gateway</html>")
        with patch.object(client, "_make_http_request", AsyncMock(return_value=cm)):
            with pytest.raises(aiohttp.ClientResponseError):
                await client._api_call("get", "https://api/x")

    async def test_api_call_body_parse_failure_falls_through(self, client):
        """When body parsing raises, fall through to the generic aiohttp error."""
        import aiohttp

        cm = self._http_error_response(502, ValueError("not json"))
        with patch.object(client, "_make_http_request", AsyncMock(return_value=cm)):
            with pytest.raises(aiohttp.ClientResponseError):
                await client._api_call("get", "https://api/x")

    async def test_api_call_network_error_propagates(self, client):
        """Network-level failure (no response) propagates unchanged."""
        import aiohttp

        with patch.object(
            client,
            "_make_http_request",
            AsyncMock(side_effect=aiohttp.ClientConnectionError("Network Error")),
        ):
            with pytest.raises(aiohttp.ClientConnectionError, match="Network Error"):
                await client._api_call("get", "https://api/x")

    async def test_api_call_non_http_error_propagates(self, client):
        """Non-HTTP exceptions raised inside the request propagate unchanged."""
        with patch.object(
            client,
            "_make_http_request",
            AsyncMock(side_effect=RuntimeError("generic non-http failure")),
        ):
            with pytest.raises(RuntimeError, match="generic non-http failure"):
                await client._api_call("get", "https://api/x")

    async def test_api_call_success_returns_json(self, client):
        """Happy path: returns parsed JSON body."""
        cm = self._http_error_response(200, [{"id": 1}])
        with patch.object(client, "_make_http_request", AsyncMock(return_value=cm)):
            data = await client._api_call("get", "https://api/x")
        assert data == [{"id": 1}]

    # -------------------------------------------------------------------- #
    # get_deployment — env / contract_type / return_abi filters            #
    # -------------------------------------------------------------------- #
    # The Dexalot REST deployment endpoint takes optional
    # env / contracttype / returnabi filters. The SDK accepts them via
    # keyword-only args and resolves defaults (env=parent_env,
    # contract_type='All', return_abi=True). The cache key includes all
    # three so filter variants do not collide on the same static-cache
    # slot.

    async def test_get_deployment_defaults_call_rest_endpoint(self, client):
        """No-args ``get_deployment`` calls REST with default filters."""
        api_spy = AsyncMock(return_value=[{"env": "fuji-multi", "contracttype": "All"}])
        with patch.object(client, "_api_call", api_spy):
            result = await client.get_deployment()

        assert result.success
        assert result.data == [{"env": "fuji-multi", "contracttype": "All"}]
        api_spy.assert_awaited_once()
        _, kwargs = api_spy.call_args
        assert kwargs["params"] == {
            "env": client.parent_env,
            "contracttype": "All",
            "returnabi": "true",
        }

    async def test_get_deployment_partial_opts_default_remaining(self, client):
        """Partial opts (only ``env``) fall back to defaults for the rest."""
        api_spy = AsyncMock(return_value=[])
        with patch.object(client, "_api_call", api_spy):
            await client.get_deployment(env="fuji-multi-subnet")

        _, kwargs = api_spy.call_args
        assert kwargs["params"] == {
            "env": "fuji-multi-subnet",
            "contracttype": "All",
            "returnabi": "true",
        }

    async def test_get_deployment_full_opts_propagate(self, client):
        """All three opts are forwarded to the REST query string."""
        api_spy = AsyncMock(return_value=[])
        with patch.object(client, "_api_call", api_spy):
            result = await client.get_deployment(
                env="fuji-multi-avax",
                contract_type="Portfolio",
                return_abi=False,
            )

        assert result.success
        assert result.data == []
        _, kwargs = api_spy.call_args
        assert kwargs["params"] == {
            "env": "fuji-multi-avax",
            "contracttype": "Portfolio",
            "returnabi": "false",
        }

    async def test_get_deployment_distinct_cache_slots_per_filter_combo(self, client):
        """Distinct (env, contract_type, return_abi) combos use distinct cache slots."""
        api_spy = AsyncMock(return_value=[])
        with patch.object(client, "_api_call", api_spy):
            await client.get_deployment(env="fuji-multi-avax")
            await client.get_deployment(env="production-multi-avax")
            await client.get_deployment(env="fuji-multi-avax", contract_type="Portfolio")
            await client.get_deployment(env="fuji-multi-avax", return_abi=False)

        # Four distinct calls — no cache collision
        assert api_spy.await_count == 4

    async def test_get_deployment_repeated_identical_call_is_cached(self, client):
        """Repeated identical calls hit the static cache and skip REST."""
        api_spy = AsyncMock(return_value=[])
        with patch.object(client, "_api_call", api_spy):
            await client.get_deployment(env="fuji-multi-avax")
            await client.get_deployment(env="fuji-multi-avax")

        assert api_spy.await_count == 1

    async def test_get_deployment_rest_failure_returns_sanitized_fail(self, client):
        """REST failures are caught and returned as Result.fail."""
        with patch.object(
            client,
            "_api_call",
            AsyncMock(side_effect=RuntimeError("FQ-015: insufficient liquidity")),
        ):
            result = await client.get_deployment(env="fuji-multi-avax")

        assert not result.success
        assert "FQ-015" in result.error or "insufficient liquidity" in result.error.lower()
