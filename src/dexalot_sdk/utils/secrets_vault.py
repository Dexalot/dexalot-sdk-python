"""Encrypted key-value secrets vault backed by SQLite.

Uses Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256). Keys are stored as
plaintext for lookup; only values are encrypted. The secrets vault file is created with
owner-only permissions (0o600).

Typical usage::

    key = generate_secrets_vault_key()
    secrets_vault_set("~/.dexalot/secrets_vault.db", "PRIVATE_KEY", "0x...", key)
    result = secrets_vault_get("~/.dexalot/secrets_vault.db", "PRIVATE_KEY", key)
    if result.success:
        private_key = result.data
"""

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .result import Result

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS secrets_vault (
    key        TEXT PRIMARY KEY,
    value      BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def generate_secrets_vault_key() -> str:
    """Generate a new Fernet encryption key.

    Returns:
        A URL-safe base64-encoded 32-byte key string suitable for use with secrets vault functions.
    """
    return Fernet.generate_key().decode()


def _open(db_path: str | Path) -> sqlite3.Connection:
    """Open (and initialise) the secrets vault database, creating it with secure permissions."""
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(_CREATE_TABLE)
    conn.commit()
    # Restrict access to owner only if this is a new file (size check avoids chmod on every open).
    try:
        current_mode = path.stat().st_mode & 0o777
        if current_mode != 0o600:
            os.chmod(path, 0o600)
    except OSError:
        pass
    return conn


def secrets_vault_set(
    db_path: str | Path, key: str, value: str, encryption_key: str
) -> Result[None]:
    """Encrypt and store (upsert) a value in the secrets vault.

    Args:
        db_path: Path to the SQLite secrets vault database file.
        key: Plaintext key name (e.g. "PRIVATE_KEY").
        value: Plaintext value to encrypt and store.
        encryption_key: Fernet key string (from :func:`generate_secrets_vault_key`).

    Returns:
        ``Result.ok(None)`` on success, ``Result.fail(msg)`` on error.
    """
    if not key:
        return Result.fail("secrets_vault_set: key must not be empty")
    if not value:
        return Result.fail("secrets_vault_set: value must not be empty")
    try:
        f = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)
        encrypted = f.encrypt(value.encode())
        now = datetime.now(UTC).isoformat()
        with _open(db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO secrets_vault (key, value, created_at, updated_at) "
                "VALUES (?, ?, COALESCE("
                "(SELECT created_at FROM secrets_vault WHERE key = ?), ?), ?)",
                (key, encrypted, key, now, now),
            )
        return Result.ok(None)
    except Exception as exc:
        return Result.fail(f"secrets_vault_set failed: {exc}")


def secrets_vault_get(db_path: str | Path, key: str, encryption_key: str) -> Result[str]:
    """Retrieve and decrypt a value from the secrets vault.

    Args:
        db_path: Path to the SQLite secrets vault database file.
        key: Plaintext key name to look up.
        encryption_key: Fernet key string used to decrypt the value.

    Returns:
        ``Result.ok(plaintext)`` on success, ``Result.fail(msg)`` if the key does not
        exist or the encryption key is wrong / data is corrupted.
    """
    if not key:
        return Result.fail("secrets_vault_get: key must not be empty")
    try:
        with _open(db_path) as conn:
            row = conn.execute("SELECT value FROM secrets_vault WHERE key = ?", (key,)).fetchone()
        if row is None:
            return Result.fail(f"secrets_vault_get: key '{key}' not found")
        f = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)
        plaintext = f.decrypt(row[0]).decode()
        return Result.ok(plaintext)
    except InvalidToken:
        return Result.fail("secrets_vault_get: decryption failed — wrong key or corrupted data")
    except Exception as exc:
        return Result.fail(f"secrets_vault_get failed: {exc}")


def secrets_vault_list(db_path: str | Path) -> Result[list[str]]:
    """List all key names stored in the secrets vault.

    The encryption key is not required — only key names (not values) are returned.

    Args:
        db_path: Path to the SQLite secrets vault database file.

    Returns:
        ``Result.ok([key, ...])`` sorted alphabetically, or ``Result.fail(msg)`` on error.
        Returns an empty list if the secrets vault exists but contains no entries.
    """
    try:
        with _open(db_path) as conn:
            rows = conn.execute("SELECT key FROM secrets_vault ORDER BY key").fetchall()
        return Result.ok([row[0] for row in rows])
    except Exception as exc:
        return Result.fail(f"secrets_vault_list failed: {exc}")


def secrets_vault_remove(db_path: str | Path, key: str) -> Result[None]:
    """Remove a key-value pair from the secrets vault.

    Args:
        db_path: Path to the SQLite secrets vault database file.
        key: Plaintext key name to remove.

    Returns:
        ``Result.ok(None)`` on success, ``Result.fail(msg)`` if the key does not exist.
    """
    if not key:
        return Result.fail("secrets_vault_remove: key must not be empty")
    try:
        with _open(db_path) as conn:
            cursor = conn.execute("DELETE FROM secrets_vault WHERE key = ?", (key,))
        if cursor.rowcount == 0:
            return Result.fail(f"secrets_vault_remove: key '{key}' not found")
        return Result.ok(None)
    except Exception as exc:
        return Result.fail(f"secrets_vault_remove failed: {exc}")
