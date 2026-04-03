"""Unit tests for the encrypted file-backed secrets vault module."""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import dexalot_sdk.utils.secrets_vault as sv_module
from dexalot_sdk.utils.secrets_vault import (
    VAULT_FILE_FORMAT,
    VAULT_FILE_VERSION,
    _create_empty_vault,
    _ensure_vault_path,
    _expand_path,
    _is_vault_entry,
    _load_vault,
    _normalize_fernet_key,
    _parse_vault_file,
    _write_vault_file,
    generate_secrets_vault_key,
    secrets_vault_get,
    secrets_vault_list,
    secrets_vault_remove,
    secrets_vault_set,
)


def _vault_path(tmp_path: Path, name: str = "secrets_vault.json") -> Path:
    return tmp_path / name


def _read_vault(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


class TestHelpers:
    def test_generate_key_returns_unpadded_base64url_string(self):
        key = generate_secrets_vault_key()
        assert isinstance(key, str)
        assert "=" not in key
        decoded = base64.urlsafe_b64decode((key + "==").encode())
        assert len(decoded) == 32

    def test_normalize_key_accepts_str_and_bytes(self):
        key = generate_secrets_vault_key()
        normalized_from_str = _normalize_fernet_key(key)
        normalized_from_bytes = _normalize_fernet_key(key.encode())
        assert normalized_from_str == normalized_from_bytes
        assert len(base64.urlsafe_b64decode(normalized_from_str)) == 32

    def test_normalize_key_rejects_wrong_length(self):
        try:
            _normalize_fernet_key("abc")
        except ValueError as exc:
            assert "32 bytes" in str(exc)
        else:
            raise AssertionError("Expected ValueError for short key")

    def test_expand_path_resolves_user_and_absolute(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        expanded = _expand_path("~/vault.db")
        assert expanded == (fake_home / "vault.db").resolve()

    def test_create_empty_vault_matches_canonical_shape(self):
        assert _create_empty_vault() == {
            "format": VAULT_FILE_FORMAT,
            "version": VAULT_FILE_VERSION,
            "entries": {},
        }

    def test_is_vault_entry_validates_shape(self):
        assert _is_vault_entry({"value": "v", "created_at": "c", "updated_at": "u"}) is True
        assert _is_vault_entry(None) is False
        assert _is_vault_entry({"value": "v", "created_at": "c"}) is False

    def test_parse_vault_file_rejects_invalid_shapes(self):
        bad_payloads = [
            "null",
            json.dumps({"format": "wrong", "version": 1, "entries": {}}),
            json.dumps({"format": VAULT_FILE_FORMAT, "version": 2, "entries": {}}),
            json.dumps({"format": VAULT_FILE_FORMAT, "version": 1, "entries": []}),
            json.dumps({"format": VAULT_FILE_FORMAT, "version": 1, "entries": {"BROKEN": None}}),
        ]
        messages = [
            "Invalid secrets vault file format",
            "Unsupported secrets vault format",
            "Unsupported secrets vault version",
            "Invalid secrets vault file format",
            "Invalid secrets vault entry for key 'BROKEN'",
        ]
        for raw, message in zip(bad_payloads, messages, strict=True):
            try:
                _parse_vault_file(raw)
            except ValueError as exc:
                assert message in str(exc)
            else:
                raise AssertionError(f"Expected ValueError containing {message!r}")

    def test_write_vault_file_writes_json_and_ignores_chmod_errors(self, tmp_path):
        path = _vault_path(tmp_path)
        data = _create_empty_vault()
        with patch("os.chmod", side_effect=[None, OSError("chmod fail")]):
            _write_vault_file(path, data)
        assert path.exists()
        assert _read_vault(path) == data

    def test_ensure_vault_path_creates_file_and_rejects_directory(self, tmp_path):
        path = _vault_path(tmp_path)
        resolved = _ensure_vault_path(path)
        assert resolved == path.resolve()
        assert path.exists()

        directory = tmp_path / "dir-vault"
        directory.mkdir()
        try:
            _ensure_vault_path(directory)
        except ValueError as exc:
            assert "directory" in str(exc)
        else:
            raise AssertionError("Expected ValueError for directory path")

    def test_load_vault_returns_path_and_data(self, tmp_path):
        path = _vault_path(tmp_path)
        resolved, data = _load_vault(path)
        assert resolved == path.resolve()
        assert data == _create_empty_vault()


class TestSecretsVaultSet:
    def test_set_creates_file_and_canonical_json_payload(self, tmp_path):
        path = _vault_path(tmp_path)
        key = generate_secrets_vault_key()
        result = secrets_vault_set(path, "PRIVATE_KEY", "0xabc", key)
        assert result.success
        payload = _read_vault(path)
        assert payload["format"] == VAULT_FILE_FORMAT
        assert payload["version"] == VAULT_FILE_VERSION
        entry = payload["entries"]["PRIVATE_KEY"]
        assert isinstance(entry["value"], str)
        assert entry["created_at"] == entry["updated_at"]
        base64.b64decode(entry["value"].encode("ascii"))

    def test_set_round_trips_with_unpadded_and_padded_keys(self, tmp_path):
        path = _vault_path(tmp_path)
        key = generate_secrets_vault_key()
        padded = _normalize_fernet_key(key).decode()
        assert secrets_vault_set(path, "K", "value", key).success
        assert secrets_vault_get(path, "K", padded).data == "value"

    def test_set_returns_ok(self, tmp_path):
        assert secrets_vault_set(
            _vault_path(tmp_path), "K", "V", generate_secrets_vault_key()
        ).success

    def test_empty_key_rejected(self, tmp_path):
        result = secrets_vault_set(_vault_path(tmp_path), "", "value", generate_secrets_vault_key())
        assert not result.success
        assert result.error == "secrets_vault_set: key must not be empty"

    def test_empty_value_rejected(self, tmp_path):
        result = secrets_vault_set(_vault_path(tmp_path), "KEY", "", generate_secrets_vault_key())
        assert not result.success
        assert result.error == "secrets_vault_set: value must not be empty"

    def test_upsert_overwrites_value_and_preserves_created_at(self, tmp_path):
        path = _vault_path(tmp_path)
        key = generate_secrets_vault_key()
        assert secrets_vault_set(path, "K", "first", key).success
        first = _read_vault(path)["entries"]["K"]
        assert secrets_vault_set(path, "K", "second", key).success
        second = _read_vault(path)["entries"]["K"]
        assert first["created_at"] == second["created_at"]
        assert second["updated_at"] >= second["created_at"]
        assert secrets_vault_get(path, "K", key).data == "second"

    def test_created_with_owner_only_permissions(self, tmp_path):
        path = _vault_path(tmp_path)
        secrets_vault_set(path, "K", "V", generate_secrets_vault_key())
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600

    def test_parent_directory_autocreated(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "secrets_vault.json"
        result = secrets_vault_set(path, "K", "V", generate_secrets_vault_key())
        assert result.success
        assert path.exists()

    def test_invalid_encryption_key_returns_fail(self, tmp_path):
        result = secrets_vault_set(_vault_path(tmp_path), "K", "V", "not-a-valid-fernet-key")
        assert not result.success
        assert "secrets_vault_set failed" in (result.error or "")

    def test_set_propagates_load_failures(self, tmp_path):
        with patch.object(sv_module, "_load_vault", side_effect=Exception("load failed")):
            result = secrets_vault_set(
                _vault_path(tmp_path), "K", "V", generate_secrets_vault_key()
            )
        assert not result.success
        assert result.error == "secrets_vault_set failed: load failed"


class TestSecretsVaultGet:
    def test_round_trip(self, tmp_path):
        path = _vault_path(tmp_path)
        enc_key = generate_secrets_vault_key()
        secrets_vault_set(path, "SECRET", "hunter2", enc_key)
        result = secrets_vault_get(path, "SECRET", enc_key)
        assert result.success
        assert result.data == "hunter2"

    def test_nonexistent_key_fails(self, tmp_path):
        path = _vault_path(tmp_path)
        secrets_vault_set(path, "OTHER", "val", generate_secrets_vault_key())
        result = secrets_vault_get(path, "MISSING", generate_secrets_vault_key())
        assert not result.success
        assert result.error == "secrets_vault_get: key 'MISSING' not found"

    def test_wrong_encryption_key_fails(self, tmp_path):
        path = _vault_path(tmp_path)
        enc_key = generate_secrets_vault_key()
        secrets_vault_set(path, "K", "secret", enc_key)
        result = secrets_vault_get(path, "K", generate_secrets_vault_key())
        assert not result.success
        assert result.error == "secrets_vault_get: decryption failed - wrong key or corrupted data"

    def test_empty_key_rejected(self, tmp_path):
        result = secrets_vault_get(_vault_path(tmp_path), "", generate_secrets_vault_key())
        assert not result.success
        assert result.error == "secrets_vault_get: key must not be empty"

    def test_multiline_value_round_trips(self, tmp_path):
        path = _vault_path(tmp_path)
        enc_key = generate_secrets_vault_key()
        value = "line1\nline2\tspecial!@#$%^&*()"
        secrets_vault_set(path, "K", value, enc_key)
        result = secrets_vault_get(path, "K", enc_key)
        assert result.success
        assert result.data == value

    def test_invalid_encryption_key_returns_fail(self, tmp_path):
        path = _vault_path(tmp_path)
        secrets_vault_set(path, "K", "secret", generate_secrets_vault_key())
        result = secrets_vault_get(path, "K", "not-a-valid-fernet-key")
        assert not result.success
        assert "secrets_vault_get failed" in (result.error or "")

    def test_corrupted_entry_returns_decryption_failed(self, tmp_path):
        path = _vault_path(tmp_path)
        key = generate_secrets_vault_key()
        secrets_vault_set(path, "K", "secret", key)
        payload = _read_vault(path)
        payload["entries"]["K"]["value"] = base64.b64encode(b"corrupted-bytes").decode("ascii")
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        result = secrets_vault_get(path, "K", key)
        assert not result.success
        assert result.error == "secrets_vault_get: decryption failed - wrong key or corrupted data"

    def test_get_propagates_load_failures(self, tmp_path):
        with patch.object(sv_module, "_load_vault", side_effect=Exception("load failed")):
            result = secrets_vault_get(_vault_path(tmp_path), "K", generate_secrets_vault_key())
        assert not result.success
        assert result.error == "secrets_vault_get failed: load failed"


class TestSecretsVaultList:
    def test_empty_vault_returns_empty_list(self, tmp_path):
        path = _vault_path(tmp_path)
        result = secrets_vault_list(path)
        assert result.success
        assert result.data == []
        assert path.exists()

    def test_lists_all_keys_alphabetically(self, tmp_path):
        path = _vault_path(tmp_path)
        key = generate_secrets_vault_key()
        secrets_vault_set(path, "ZEBRA", "z", key)
        secrets_vault_set(path, "ALPHA", "a", key)
        secrets_vault_set(path, "MIDDLE", "m", key)
        result = secrets_vault_list(path)
        assert result.success
        assert result.data == ["ALPHA", "MIDDLE", "ZEBRA"]

    def test_list_propagates_failures(self, tmp_path):
        with patch.object(sv_module, "_load_vault", side_effect=Exception("load failed")):
            result = secrets_vault_list(_vault_path(tmp_path))
        assert not result.success
        assert result.error == "secrets_vault_list failed: load failed"


class TestSecretsVaultRemove:
    def test_remove_existing_key(self, tmp_path):
        path = _vault_path(tmp_path)
        key = generate_secrets_vault_key()
        secrets_vault_set(path, "K", "V", key)
        result = secrets_vault_remove(path, "K")
        assert result.success
        assert secrets_vault_list(path).data == []

    def test_remove_nonexistent_key_fails(self, tmp_path):
        result = secrets_vault_remove(_vault_path(tmp_path), "GHOST")
        assert not result.success
        assert result.error == "secrets_vault_remove: key 'GHOST' not found"

    def test_remove_empty_key_rejected(self, tmp_path):
        result = secrets_vault_remove(_vault_path(tmp_path), "")
        assert not result.success
        assert result.error == "secrets_vault_remove: key must not be empty"

    def test_remove_does_not_affect_other_keys(self, tmp_path):
        path = _vault_path(tmp_path)
        enc_key = generate_secrets_vault_key()
        secrets_vault_set(path, "A", "val_a", enc_key)
        secrets_vault_set(path, "B", "val_b", enc_key)
        secrets_vault_remove(path, "A")
        assert secrets_vault_list(path).data == ["B"]
        assert secrets_vault_get(path, "B", enc_key).data == "val_b"

    def test_remove_propagates_load_failures(self, tmp_path):
        with patch.object(sv_module, "_load_vault", side_effect=Exception("load failed")):
            result = secrets_vault_remove(_vault_path(tmp_path), "K")
        assert not result.success
        assert result.error == "secrets_vault_remove failed: load failed"


class TestPackageExports:
    def test_secrets_vault_functions_exported_from_package(self):
        import dexalot_sdk

        assert hasattr(dexalot_sdk, "generate_secrets_vault_key")
        assert hasattr(dexalot_sdk, "secrets_vault_set")
        assert hasattr(dexalot_sdk, "secrets_vault_get")
        assert hasattr(dexalot_sdk, "secrets_vault_list")
        assert hasattr(dexalot_sdk, "secrets_vault_remove")
