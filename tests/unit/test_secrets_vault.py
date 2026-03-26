"""Unit tests for the encrypted secrets vault module (dexalot_sdk.utils.secrets_vault)."""

import os
import stat
from unittest.mock import patch

import dexalot_sdk.utils.secrets_vault as sv_module
from dexalot_sdk.utils.secrets_vault import (
    generate_secrets_vault_key,
    secrets_vault_get,
    secrets_vault_list,
    secrets_vault_remove,
    secrets_vault_set,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db(tmp_path, name="secrets_vault.db"):
    return tmp_path / name


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


class TestGenerateSecretsVaultKey:
    def test_returns_nonempty_string(self):
        """generate_secrets_vault_key returns a non-empty string."""
        key = generate_secrets_vault_key()
        assert isinstance(key, str)
        assert len(key) > 0

    def test_each_call_returns_unique_key(self):
        """Two calls produce different keys."""
        assert generate_secrets_vault_key() != generate_secrets_vault_key()

    def test_key_usable_with_fernet(self):
        """The generated key is accepted by Fernet without raising."""
        from cryptography.fernet import Fernet

        key = generate_secrets_vault_key()
        Fernet(key.encode())  # raises if invalid


# ---------------------------------------------------------------------------
# secrets_vault_set
# ---------------------------------------------------------------------------


class TestSecretsVaultSet:
    def test_set_creates_db_file(self, tmp_path):
        """secrets_vault_set creates the database file on first call."""
        db = _db(tmp_path)
        assert not db.exists()
        result = secrets_vault_set(db, "MY_KEY", "my_value", generate_secrets_vault_key())
        assert result.success
        assert db.exists()

    def test_set_returns_ok(self, tmp_path):
        """secrets_vault_set returns Result.ok on success."""
        result = secrets_vault_set(_db(tmp_path), "K", "V", generate_secrets_vault_key())
        assert result.success
        assert result.error is None

    def test_empty_key_rejected(self, tmp_path):
        """secrets_vault_set rejects an empty key."""
        result = secrets_vault_set(_db(tmp_path), "", "value", generate_secrets_vault_key())
        assert not result.success
        assert result.error is not None

    def test_empty_value_rejected(self, tmp_path):
        """secrets_vault_set rejects an empty value."""
        result = secrets_vault_set(_db(tmp_path), "KEY", "", generate_secrets_vault_key())
        assert not result.success
        assert result.error is not None

    def test_upsert_overwrites_existing_key(self, tmp_path):
        """secrets_vault_set overwrites an existing entry when the key already exists."""
        db = _db(tmp_path)
        key = generate_secrets_vault_key()
        secrets_vault_set(db, "MYKEY", "first", key)
        secrets_vault_set(db, "MYKEY", "second", key)
        result = secrets_vault_get(db, "MYKEY", key)
        assert result.success
        assert result.data == "second"

    def test_db_created_with_owner_only_permissions(self, tmp_path):
        """secrets_vault_set sets the secrets vault file permissions to 0o600."""
        db = _db(tmp_path)
        secrets_vault_set(db, "K", "V", generate_secrets_vault_key())
        mode = stat.S_IMODE(os.stat(db).st_mode)
        assert mode == 0o600

    def test_parent_directory_autocreated(self, tmp_path):
        """secrets_vault_set creates nested parent directories automatically."""
        db = tmp_path / "deep" / "nested" / "secrets_vault.db"
        result = secrets_vault_set(db, "K", "V", generate_secrets_vault_key())
        assert result.success
        assert db.exists()

    def test_invalid_encryption_key_returns_fail(self, tmp_path):
        """secrets_vault_set returns Result.fail when given a malformed encryption key."""
        result = secrets_vault_set(_db(tmp_path), "K", "V", "not-a-valid-fernet-key")
        assert not result.success
        assert "secrets_vault_set failed" in (result.error or "")

    def test_chmod_oserror_is_silently_ignored(self, tmp_path):
        """An OSError raised by chmod during DB creation does not fail the operation."""
        db = _db(tmp_path)
        with patch("os.chmod", side_effect=OSError("permission denied")):
            result = secrets_vault_set(db, "K", "V", generate_secrets_vault_key())
        assert result.success


# ---------------------------------------------------------------------------
# secrets_vault_get
# ---------------------------------------------------------------------------


class TestSecretsVaultGet:
    def test_round_trip(self, tmp_path):
        """secrets_vault_set followed by secrets_vault_get returns the original plaintext."""
        db = _db(tmp_path)
        enc_key = generate_secrets_vault_key()
        secrets_vault_set(db, "SECRET", "hunter2", enc_key)
        result = secrets_vault_get(db, "SECRET", enc_key)
        assert result.success
        assert result.data == "hunter2"

    def test_nonexistent_key_fails(self, tmp_path):
        """secrets_vault_get returns Result.fail for a key that was never stored."""
        db = _db(tmp_path)
        secrets_vault_set(db, "OTHER", "val", generate_secrets_vault_key())
        result = secrets_vault_get(db, "MISSING", generate_secrets_vault_key())
        assert not result.success
        assert result.error is not None

    def test_wrong_encryption_key_fails(self, tmp_path):
        """secrets_vault_get returns Result.fail when decryption key does not match."""
        db = _db(tmp_path)
        enc_key = generate_secrets_vault_key()
        secrets_vault_set(db, "K", "secret", enc_key)
        wrong_key = generate_secrets_vault_key()
        result = secrets_vault_get(db, "K", wrong_key)
        assert not result.success
        assert "decryption failed" in (result.error or "")

    def test_empty_key_rejected(self, tmp_path):
        """secrets_vault_get rejects an empty key."""
        result = secrets_vault_get(_db(tmp_path), "", generate_secrets_vault_key())
        assert not result.success

    def test_multiline_value_round_trips(self, tmp_path):
        """secrets_vault_get handles values containing newlines and special characters."""
        db = _db(tmp_path)
        enc_key = generate_secrets_vault_key()
        value = "line1\nline2\tspecial!@#$%^&*()"
        secrets_vault_set(db, "K", value, enc_key)
        result = secrets_vault_get(db, "K", enc_key)
        assert result.success
        assert result.data == value

    def test_invalid_encryption_key_returns_fail(self, tmp_path):
        """secrets_vault_get returns Result.fail when the key format is not valid Fernet."""
        db = _db(tmp_path)
        secrets_vault_set(db, "K", "secret", generate_secrets_vault_key())
        result = secrets_vault_get(db, "K", "not-a-valid-fernet-key")
        assert not result.success
        assert "secrets_vault_get failed" in (result.error or "")


# ---------------------------------------------------------------------------
# secrets_vault_list
# ---------------------------------------------------------------------------


class TestSecretsVaultList:
    def test_empty_secrets_vault_returns_empty_list(self, tmp_path):
        """secrets_vault_list returns an empty list for a freshly created secrets vault."""
        db = _db(tmp_path)
        secrets_vault_set(db, "SEED", "v", generate_secrets_vault_key())
        secrets_vault_remove(db, "SEED")
        result = secrets_vault_list(db)
        assert result.success
        assert result.data == []

    def test_lists_all_keys_alphabetically(self, tmp_path):
        """secrets_vault_list returns all stored keys in alphabetical order."""
        db = _db(tmp_path)
        enc_key = generate_secrets_vault_key()
        secrets_vault_set(db, "ZEBRA", "z", enc_key)
        secrets_vault_set(db, "ALPHA", "a", enc_key)
        secrets_vault_set(db, "MIDDLE", "m", enc_key)
        result = secrets_vault_list(db)
        assert result.success
        assert result.data == ["ALPHA", "MIDDLE", "ZEBRA"]

    def test_list_does_not_require_encryption_key(self, tmp_path):
        """secrets_vault_list works without supplying an encryption key."""
        db = _db(tmp_path)
        secrets_vault_set(db, "K", "V", generate_secrets_vault_key())
        result = secrets_vault_list(db)
        assert result.success
        assert "K" in (result.data or [])

    def test_list_on_new_path_creates_secrets_vault_and_returns_empty(self, tmp_path):
        """secrets_vault_list on a non-existent path creates the file and returns []."""
        db = tmp_path / "new_secrets_vault.db"
        result = secrets_vault_list(db)
        assert result.success
        assert result.data == []
        assert db.exists()

    def test_db_error_returns_fail(self, tmp_path):
        """secrets_vault_list returns Result.fail when the underlying database raises."""
        with patch.object(sv_module, "_open", side_effect=Exception("db unavailable")):
            result = secrets_vault_list(_db(tmp_path))
        assert not result.success
        assert "secrets_vault_list failed" in (result.error or "")


# ---------------------------------------------------------------------------
# secrets_vault_remove
# ---------------------------------------------------------------------------


class TestSecretsVaultRemove:
    def test_remove_existing_key(self, tmp_path):
        """secrets_vault_remove deletes an existing key successfully."""
        db = _db(tmp_path)
        enc_key = generate_secrets_vault_key()
        secrets_vault_set(db, "K", "V", enc_key)
        result = secrets_vault_remove(db, "K")
        assert result.success
        assert not secrets_vault_get(db, "K", enc_key).success

    def test_remove_nonexistent_key_fails(self, tmp_path):
        """secrets_vault_remove returns Result.fail when the key does not exist."""
        result = secrets_vault_remove(_db(tmp_path), "GHOST")
        assert not result.success
        assert result.error is not None

    def test_remove_empty_key_rejected(self, tmp_path):
        """secrets_vault_remove rejects an empty key."""
        result = secrets_vault_remove(_db(tmp_path), "")
        assert not result.success

    def test_remove_does_not_affect_other_keys(self, tmp_path):
        """secrets_vault_remove only removes the targeted key, leaving others intact."""
        db = _db(tmp_path)
        enc_key = generate_secrets_vault_key()
        secrets_vault_set(db, "A", "val_a", enc_key)
        secrets_vault_set(db, "B", "val_b", enc_key)
        secrets_vault_remove(db, "A")
        list_result = secrets_vault_list(db)
        assert list_result.success
        assert list_result.data == ["B"]
        get_result = secrets_vault_get(db, "B", enc_key)
        assert get_result.success
        assert get_result.data == "val_b"

    def test_db_error_returns_fail(self, tmp_path):
        """secrets_vault_remove returns Result.fail when the underlying database raises."""
        with patch.object(sv_module, "_open", side_effect=Exception("db unavailable")):
            result = secrets_vault_remove(_db(tmp_path), "K")
        assert not result.success
        assert "secrets_vault_remove failed" in (result.error or "")


# ---------------------------------------------------------------------------
# Cross-cutting: created_at preservation on upsert
# ---------------------------------------------------------------------------


class TestSecretsVaultUpsertTimestamps:
    def test_created_at_preserved_on_upsert(self, tmp_path):
        """Updating a key preserves the original created_at timestamp."""
        import sqlite3

        db = _db(tmp_path)
        enc_key = generate_secrets_vault_key()
        secrets_vault_set(db, "K", "first", enc_key)
        conn = sqlite3.connect(str(db))
        original_created = conn.execute(
            "SELECT created_at FROM secrets_vault WHERE key='K'"
        ).fetchone()[0]
        conn.close()

        secrets_vault_set(db, "K", "second", enc_key)
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT created_at, updated_at FROM secrets_vault WHERE key='K'"
        ).fetchone()
        conn.close()

        assert row[0] == original_created, "created_at should not change on upsert"
        assert row[1] >= original_created, "updated_at should be >= created_at"


# ---------------------------------------------------------------------------
# Public API exported from package root
# ---------------------------------------------------------------------------


class TestPackageExports:
    def test_secrets_vault_functions_exported_from_package(self):
        """Secrets vault functions are accessible from the dexalot_sdk top-level package."""
        import dexalot_sdk

        assert hasattr(dexalot_sdk, "generate_secrets_vault_key")
        assert hasattr(dexalot_sdk, "secrets_vault_set")
        assert hasattr(dexalot_sdk, "secrets_vault_get")
        assert hasattr(dexalot_sdk, "secrets_vault_list")
        assert hasattr(dexalot_sdk, "secrets_vault_remove")
