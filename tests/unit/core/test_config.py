import os
from unittest.mock import patch

import pytest

from dexalot_sdk import DexalotClient
from dexalot_sdk.core.config import DexalotConfig
from tests.unit.utils.string_assertions import assert_contains


class TestDexalotConfig:
    """Test DexalotConfig and configuration loading."""

    def test_default_load(self):
        """Test default configuration loading."""
        # Clean environment for this test to rely on defaults/file
        with patch.dict(os.environ, {}, clear=True):
            # We need to mock load_dotenv to avoid picking up local .env if we want pure defaults,
            # OR we assume the environment is what it is.
            # verify_config.py ran against the actual environment.
            # For a unit test, we should control the environment.

            # Let's mock defaults by clearing env vars specific to Dexalot
            # We can't clear ALL env vars easily without unexpected side effects maybe?
            # actually patch.dict(os.environ, {}, clear=True) clears everything.

            # The verify_config.py script showed:
            # Parent Env: fuji-multi
            # API URL: https://api.dexalot-test.com (which implies defaults worked)

            # Let's mock load_dotenv to do nothing so we test pure class defaults
            with patch("dexalot_sdk.core.config.load_dotenv"):
                config = DexalotConfig.from_env()
                assert config.parent_env == "fuji-multi"
                # Base URL logic: defaults to None in class, but from_env sets it based on parent_env
                assert_contains(config.api_base_url, "dexalot-test.com")
                assert config.enable_cache is True

    def test_constructor_arg_override(self):
        """Test that constructor arguments override default values."""
        # Equivalent to: client2 = DexalotClient(parent_env="production-multi-avax")

        with patch.dict(os.environ, {}, clear=True):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                client = DexalotClient(parent_env="production-multi-avax")
                assert client.config.parent_env == "production-multi-avax"
                assert_contains(client.config.api_base_url, "api.dexalot.com")
                # Other defaults remain
                assert client.config.enable_cache is True

    def test_config_object_load(self):
        """Test passing a DexalotConfig object directly."""
        with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                config = DexalotConfig.from_env()
                config.parent_env = "custom-env"
                # We need to manually set api_url if we change parent_env on an existing object
                # because from_env logic ran already.
                # But DexalotBaseClient uses config.api_base_url.
                config.api_base_url = "https://custom.api"

                client = DexalotClient(config=config)
                assert client.config.parent_env == "custom-env"
                assert client.config.api_base_url == "https://custom.api"

    def test_precedence_arg_vs_env(self):
        """Test that constructor arguments take precedence over environment variables."""
        # os.environ["PARENTENV"] = "env-var-env"
        # client4 = DexalotClient(parent_env="arg-env")

        env_vars = {"PARENTENV": "env-var-env", "PRIVATE_KEY": "0x" + "a" * 64}
        with patch.dict(os.environ, env_vars):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                client = DexalotClient(parent_env="arg-env")
                # Arg > Env
                assert client.config.parent_env == "arg-env"

    def test_precedence_env_vs_default(self):
        """Test that environment variables take precedence over defaults."""
        env_vars = {"PARENTENV": "env-var-env", "PRIVATE_KEY": "0x" + "a" * 64}
        with patch.dict(os.environ, env_vars):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                client = DexalotClient()
                assert client.config.parent_env == "env-var-env"

    def test_api_url_derived_logic(self):
        """Test the logic for deriving API URL from parent_env."""
        # Case 1: fuji -> testnet
        with patch.dict(
            os.environ, {"API_BASE_URL_TESTNET": "http://testnet", "PARENTENV": "fuji-custom"}
        ):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                cfg = DexalotConfig.from_env()
                assert cfg.api_base_url == "http://testnet"

        # Case 2: other -> mainnet
        with patch.dict(
            os.environ, {"API_BASE_URL_MAINNET": "http://mainnet", "PARENTENV": "prod-custom"}
        ):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                cfg = DexalotConfig.from_env()
                assert cfg.api_base_url == "http://mainnet"

    def test_env_bool_parsing(self):
        """Test parsing of boolean environment variables."""
        with patch.dict(os.environ, {"DEXALOT_ENABLE_CACHE": "false"}):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                cfg = DexalotConfig.from_env()
                assert cfg.enable_cache is False

    def test_env_int_parsing_error(self):
        """Test parsing of integer environment variables with invalid input."""
        with patch.dict(os.environ, {"DEXALOT_CACHE_TTL_STATIC": "invalid"}):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                cfg = DexalotConfig.from_env()
                assert cfg.cache_ttl_static == 3600

    def test_validate_empty_parent_env(self):
        """Test validation raises error when parent_env is empty."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                cfg = DexalotConfig(parent_env="")
                with pytest.raises(ValueError, match="parent_env cannot be empty"):
                    cfg.validate()

    def test_logging_config(self):
        """Test logging configuration parsing."""
        with patch.dict(os.environ, {"DEXALOT_LOG_LEVEL": "DEBUG", "DEXALOT_LOG_FORMAT": "json"}):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                cfg = DexalotConfig.from_env()
                assert cfg.log_level == "DEBUG"
                assert cfg.log_format == "json"

    def test_load_dotenv_fallback(self):
        """Test load_dotenv fallback when no .env file found."""
        with patch("dexalot_sdk.core.config.os.path.exists", return_value=False):
            with patch("dexalot_sdk.core.config.load_dotenv") as mock_load:
                DexalotConfig.from_env()
                assert mock_load.called
                assert mock_load.call_args[1]["override"] is False

    def test_env_float_parsing_error(self):
        """Test parsing of float environment variables with invalid input."""
        with patch.dict(os.environ, {"DEXALOT_RETRY_INITIAL_DELAY": "invalid"}):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                cfg = DexalotConfig.from_env()
                assert cfg.retry_initial_delay == 1.0

    def test_validate_retry_max_attempts(self):
        """Test validation raises error when retry_max_attempts < 1."""
        cfg = DexalotConfig(retry_max_attempts=0)
        with pytest.raises(ValueError, match="retry_max_attempts must be at least 1"):
            cfg.validate()

    def test_validate_retry_initial_delay(self):
        """Test validation raises error when retry_initial_delay < 0."""
        cfg = DexalotConfig(retry_initial_delay=-1.0)
        with pytest.raises(ValueError, match="retry_initial_delay must be non-negative"):
            cfg.validate()

    def test_validate_retry_max_delay(self):
        """Test validation raises error when retry_max_delay < retry_initial_delay."""
        cfg = DexalotConfig(retry_initial_delay=5.0, retry_max_delay=3.0)
        with pytest.raises(ValueError, match="retry_max_delay must be >= retry_initial_delay"):
            cfg.validate()

    def test_validate_retry_exponential_base(self):
        """Test validation raises error when retry_exponential_base < 1.0."""
        cfg = DexalotConfig(retry_exponential_base=0.5)
        with pytest.raises(ValueError, match="retry_exponential_base must be >= 1.0"):
            cfg.validate()

    def test_validate_rate_limit_requests_per_second(self):
        """Test validation raises error when rate_limit_requests_per_second <= 0."""
        cfg = DexalotConfig(rate_limit_requests_per_second=0)
        with pytest.raises(ValueError, match="rate_limit_requests_per_second must be positive"):
            cfg.validate()

    def test_validate_rate_limit_rpc_per_second(self):
        """Test validation raises error when rate_limit_rpc_per_second <= 0."""
        cfg = DexalotConfig(rate_limit_rpc_per_second=-1.0)
        with pytest.raises(ValueError, match="rate_limit_rpc_per_second must be positive"):
            cfg.validate()

    def test_websocket_config_defaults(self):
        """Test WebSocket configuration defaults."""
        cfg = DexalotConfig()
        assert cfg.ws_manager_enabled is False
        assert cfg.ws_ping_interval == 30
        assert cfg.ws_ping_timeout == 10
        assert cfg.ws_reconnect_initial_delay == 1.0
        assert cfg.ws_reconnect_max_delay == 60.0
        assert cfg.ws_reconnect_exponential_base == 2.0
        assert cfg.ws_reconnect_max_attempts == 10

    def test_websocket_config_from_env(self):
        """Test WebSocket configuration loading from environment variables."""
        env_vars = {
            "DEXALOT_WS_MANAGER_ENABLED": "true",
            "DEXALOT_WS_PING_INTERVAL": "60",
            "DEXALOT_WS_PING_TIMEOUT": "20",
            "DEXALOT_WS_RECONNECT_INITIAL_DELAY": "2.0",
            "DEXALOT_WS_RECONNECT_MAX_DELAY": "120.0",
            "DEXALOT_WS_RECONNECT_EXPONENTIAL_BASE": "3.0",
            "DEXALOT_WS_RECONNECT_MAX_ATTEMPTS": "5",
        }
        with patch.dict(os.environ, env_vars):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                cfg = DexalotConfig.from_env()
                assert cfg.ws_manager_enabled is True
                assert cfg.ws_ping_interval == 60
                assert cfg.ws_ping_timeout == 20
                assert cfg.ws_reconnect_initial_delay == 2.0
                assert cfg.ws_reconnect_max_delay == 120.0
                assert cfg.ws_reconnect_exponential_base == 3.0
                assert cfg.ws_reconnect_max_attempts == 5

    def test_validate_ws_ping_interval(self):
        """Test validation raises error when ws_ping_interval < 1."""
        cfg = DexalotConfig(ws_ping_interval=0)
        with pytest.raises(ValueError, match="ws_ping_interval must be at least 1"):
            cfg.validate()

    def test_validate_ws_ping_timeout(self):
        """Test validation raises error when ws_ping_timeout < 1."""
        cfg = DexalotConfig(ws_ping_timeout=0)
        with pytest.raises(ValueError, match="ws_ping_timeout must be at least 1"):
            cfg.validate()

    def test_validate_ws_reconnect_initial_delay(self):
        """Test validation raises error when ws_reconnect_initial_delay < 0."""
        cfg = DexalotConfig(ws_reconnect_initial_delay=-1.0)
        with pytest.raises(ValueError, match="ws_reconnect_initial_delay must be non-negative"):
            cfg.validate()

    def test_validate_ws_reconnect_max_delay(self):
        """Test validation raises error when ws_reconnect_max_delay < ws_reconnect_initial_delay."""
        cfg = DexalotConfig(ws_reconnect_initial_delay=5.0, ws_reconnect_max_delay=3.0)
        with pytest.raises(
            ValueError, match="ws_reconnect_max_delay must be >= ws_reconnect_initial_delay"
        ):
            cfg.validate()

    def test_validate_ws_reconnect_exponential_base(self):
        """Test validation raises error when ws_reconnect_exponential_base < 1.0."""
        cfg = DexalotConfig(ws_reconnect_exponential_base=0.5)
        with pytest.raises(ValueError, match="ws_reconnect_exponential_base must be >= 1.0"):
            cfg.validate()

    def test_validate_ws_reconnect_max_attempts(self):
        """Test validation raises error when ws_reconnect_max_attempts < 0."""
        cfg = DexalotConfig(ws_reconnect_max_attempts=-1)
        with pytest.raises(ValueError, match="ws_reconnect_max_attempts must be non-negative"):
            cfg.validate()

    def test_validate_connection_pool_edge_cases(self):
        """Test validation of connection pool edge cases."""
        # Test connection_pool_limit < 1
        cfg = DexalotConfig(connection_pool_limit=0)
        with pytest.raises(ValueError, match="connection_pool_limit must be at least 1"):
            cfg.validate()

        # Test connection_pool_limit_per_host < 1
        cfg = DexalotConfig(connection_pool_limit_per_host=0)
        with pytest.raises(ValueError, match="connection_pool_limit_per_host must be at least 1"):
            cfg.validate()

        # Test connection_pool_limit_per_host > connection_pool_limit
        cfg = DexalotConfig(connection_pool_limit=10, connection_pool_limit_per_host=20)
        with pytest.raises(
            ValueError, match="connection_pool_limit_per_host must be <= connection_pool_limit"
        ):
            cfg.validate()

    def test_validate_private_key_length(self):
        """Test validation of private key length."""
        # Valid private key: "0x" + 64 hex chars = 66 total characters (32 bytes)
        valid_key = "0x" + "a" * 64
        cfg = DexalotConfig(private_key=valid_key)
        cfg.validate()  # Should not raise

        # Invalid: missing "0x" prefix
        invalid_key_no_prefix = "a" * 66
        cfg = DexalotConfig(private_key=invalid_key_no_prefix)
        with pytest.raises(ValueError, match='private_key must start with "0x"'):
            cfg.validate()

        # Invalid: too short (less than 66 characters total)
        short_key = "0x" + "a" * 30  # Total: 32 characters
        cfg = DexalotConfig(private_key=short_key)
        with pytest.raises(ValueError, match="private_key must be 66 characters"):
            cfg.validate()

        # Invalid: too long (more than 66 characters total)
        long_key = "0x" + "a" * 100  # Total: 102 characters
        cfg = DexalotConfig(private_key=long_key)
        with pytest.raises(ValueError, match="private_key must be 66 characters"):
            cfg.validate()

    def test_validate_provider_failover_cooldown(self):
        """Test validation raises error when provider_failover_cooldown < 0."""
        cfg = DexalotConfig(provider_failover_cooldown=-1)
        with pytest.raises(ValueError, match="provider_failover_cooldown must be non-negative"):
            cfg.validate()

    def test_validate_provider_failover_max_failures(self):
        """Test validation raises error when provider_failover_max_failures < 1."""
        cfg = DexalotConfig(provider_failover_max_failures=0)
        with pytest.raises(ValueError, match="provider_failover_max_failures must be at least 1"):
            cfg.validate()

    def test_allow_insecure_rpc_default_is_false(self):
        """Test that allow_insecure_rpc defaults to False."""
        cfg = DexalotConfig()
        assert cfg.allow_insecure_rpc is False

    def test_allow_insecure_rpc_env_var_true(self):
        """Test that DEXALOT_ALLOW_INSECURE_RPC=true sets allow_insecure_rpc to True."""
        with patch.dict(os.environ, {"DEXALOT_ALLOW_INSECURE_RPC": "true"}):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                cfg = DexalotConfig.from_env()
                assert cfg.allow_insecure_rpc is True

    def test_allow_insecure_rpc_env_var_false(self):
        """Test that DEXALOT_ALLOW_INSECURE_RPC=false sets allow_insecure_rpc to False."""
        with patch.dict(os.environ, {"DEXALOT_ALLOW_INSECURE_RPC": "false"}):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                cfg = DexalotConfig.from_env()
                assert cfg.allow_insecure_rpc is False

    def test_from_env_dotenv_file_found_and_loaded(self):
        """When a .env file is found, load_dotenv is called with that path and break is taken."""
        with patch.dict(os.environ, {}, clear=True):
            # Return True only for the first os.path.exists call (the project-root .env check).
            call_count = {"n": 0}

            def exists_first_only(path: str) -> bool:
                call_count["n"] += 1
                return call_count["n"] == 1

            with patch("dexalot_sdk.core.config.os.path.exists", side_effect=exists_first_only):
                with patch("dexalot_sdk.core.config.load_dotenv") as mock_load:
                    DexalotConfig.from_env()
                    call_kwargs = mock_load.call_args
                    assert call_kwargs is not None
                    assert call_kwargs.kwargs.get("override") is False
                    assert "dotenv_path" in call_kwargs.kwargs
