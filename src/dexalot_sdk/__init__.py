# dexalot_sdk - Dexalot Python SDK

from .core.client import DexalotClient
from .utils.cache import MemoryCache
from .utils.secrets_vault import (
    generate_secrets_vault_key,
    secrets_vault_get,
    secrets_vault_list,
    secrets_vault_remove,
    secrets_vault_set,
)

__version__ = "0.5.5"


def get_version() -> str:
    """Return the current SDK version string."""
    return __version__


__all__ = [
    "DexalotClient",
    "MemoryCache",
    "get_version",
    "generate_secrets_vault_key",
    "secrets_vault_get",
    "secrets_vault_list",
    "secrets_vault_remove",
    "secrets_vault_set",
]
