"""Encrypted key-value secrets vault backed by a local file.

Uses Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256) in the standard
Dexalot operator vault wire format so encrypted values are portable across tooling.

The vault file stores a language-neutral JSON document so Python and TypeScript
SDKs can share the exact same on-disk format.

The vault file is created with owner-only permissions (0o600).

Typical usage::

    key = generate_secrets_vault_key()
    secrets_vault_set("~/.dexalot/secrets_vault.json", "PRIVATE_KEY", "0x...", key)
    result = secrets_vault_get("~/.dexalot/secrets_vault.json", "PRIVATE_KEY", key)
    if result.success:
        private_key = result.data
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict, cast

from cryptography.fernet import Fernet, InvalidToken

from .result import Result

VAULT_FILE_FORMAT = "dexalot-secrets-vault"
VAULT_FILE_VERSION = 1


class VaultEntry(TypedDict):
    value: str
    created_at: str
    updated_at: str


class VaultFile(TypedDict):
    format: str
    version: int
    entries: dict[str, VaultEntry]


def generate_secrets_vault_key() -> str:
    """Generate a new Fernet encryption key.

    Returns:
        A URL-safe base64-encoded 32-byte key string without padding.
    """
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def _normalize_fernet_key(encryption_key: str | bytes) -> bytes:
    if isinstance(encryption_key, bytes):
        encryption_key = encryption_key.decode()
    padded = encryption_key + ("=" * (-len(encryption_key) % 4))
    key_bytes = base64.urlsafe_b64decode(padded.encode())
    if len(key_bytes) != 32:
        raise ValueError(f"Fernet key must be 32 bytes, got {len(key_bytes)}")
    return base64.urlsafe_b64encode(key_bytes)


def _expand_path(vault_path: str | Path) -> Path:
    return Path(vault_path).expanduser().resolve()


def _create_empty_vault() -> VaultFile:
    return {
        "format": VAULT_FILE_FORMAT,
        "version": VAULT_FILE_VERSION,
        "entries": {},
    }


def _is_vault_entry(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("value"), str)
        and isinstance(value.get("created_at"), str)
        and isinstance(value.get("updated_at"), str)
    )


def _parse_vault_file(raw: str) -> VaultFile:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Invalid secrets vault file format")
    if parsed.get("format") != VAULT_FILE_FORMAT:
        raise ValueError(f"Unsupported secrets vault format: {parsed.get('format')}")
    if parsed.get("version") != VAULT_FILE_VERSION:
        raise ValueError(f"Unsupported secrets vault version: {parsed.get('version')}")

    entries = parsed.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("Invalid secrets vault file format")

    normalized_entries: dict[str, VaultEntry] = {}
    for key, value in entries.items():
        if not _is_vault_entry(value):
            raise ValueError(f"Invalid secrets vault entry for key '{key}'")
        normalized_entries[key] = cast(VaultEntry, value)

    return {
        "format": VAULT_FILE_FORMAT,
        "version": VAULT_FILE_VERSION,
        "entries": normalized_entries,
    }


def _write_vault_file(resolved_path: Path, data: VaultFile) -> None:
    payload = json.dumps(data, indent=2) + "\n"
    temp_path = resolved_path.with_name(
        f"{resolved_path.name}.tmp-{os.getpid()}-{int(datetime.now(UTC).timestamp() * 1000)}"
    )
    temp_path.write_text(payload, encoding="utf-8")
    os.chmod(temp_path, 0o600)
    temp_path.replace(resolved_path)
    try:
        os.chmod(resolved_path, 0o600)
    except OSError:
        pass


def _ensure_vault_path(vault_path: str | Path) -> Path:
    resolved_path = _expand_path(vault_path)
    resolved_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)

    if resolved_path.exists():
        if resolved_path.is_dir():
            raise ValueError("vault path is a directory")
        return resolved_path

    _write_vault_file(resolved_path, _create_empty_vault())
    return resolved_path


def _load_vault(vault_path: str | Path) -> tuple[Path, VaultFile]:
    resolved_path = _ensure_vault_path(vault_path)
    return resolved_path, _parse_vault_file(resolved_path.read_text(encoding="utf-8"))


def secrets_vault_set(
    vault_path: str | Path, key: str, value: str, encryption_key: str
) -> Result[None]:
    """Encrypt and store (upsert) a value in the secrets vault."""
    if not key:
        return Result.fail("secrets_vault_set: key must not be empty")
    if not value:
        return Result.fail("secrets_vault_set: value must not be empty")

    try:
        fernet = Fernet(_normalize_fernet_key(encryption_key))
        token = fernet.encrypt(value.encode("utf-8"))
        encrypted = base64.b64encode(base64.urlsafe_b64decode(token)).decode("ascii")
        now = datetime.now(UTC).isoformat()
        resolved_path, data = _load_vault(vault_path)
        existing = data["entries"].get(key)
        data["entries"][key] = {
            "value": encrypted,
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
        }
        _write_vault_file(resolved_path, data)
        return Result.ok(None)
    except Exception as exc:
        return Result.fail(f"secrets_vault_set failed: {exc}")


def secrets_vault_get(vault_path: str | Path, key: str, encryption_key: str) -> Result[str]:
    """Retrieve and decrypt a value from the secrets vault."""
    if not key:
        return Result.fail("secrets_vault_get: key must not be empty")

    try:
        _, data = _load_vault(vault_path)
        entry = data["entries"].get(key)
        if entry is None:
            return Result.fail(f"secrets_vault_get: key '{key}' not found")
        fernet = Fernet(_normalize_fernet_key(encryption_key))
        raw_token = base64.b64decode(entry["value"].encode("ascii"))
        token = base64.urlsafe_b64encode(raw_token)
        return Result.ok(fernet.decrypt(token).decode("utf-8"))
    except InvalidToken:
        return Result.fail("secrets_vault_get: decryption failed - wrong key or corrupted data")
    except Exception as exc:
        return Result.fail(f"secrets_vault_get failed: {exc}")


def secrets_vault_list(vault_path: str | Path) -> Result[list[str]]:
    """List all key names stored in the secrets vault."""
    try:
        _, data = _load_vault(vault_path)
        return Result.ok(sorted(data["entries"].keys()))
    except Exception as exc:
        return Result.fail(f"secrets_vault_list failed: {exc}")


def secrets_vault_remove(vault_path: str | Path, key: str) -> Result[None]:
    """Remove a key-value pair from the secrets vault."""
    if not key:
        return Result.fail("secrets_vault_remove: key must not be empty")

    try:
        resolved_path, data = _load_vault(vault_path)
        if key not in data["entries"]:
            return Result.fail(f"secrets_vault_remove: key '{key}' not found")
        del data["entries"][key]
        _write_vault_file(resolved_path, data)
        return Result.ok(None)
    except Exception as exc:
        return Result.fail(f"secrets_vault_remove failed: {exc}")
