"""Tests for the secrets-vault CLI shipped at
``dexalot_sdk.scripts.secrets_vault_cli``.

The CLI is thin glue: it routes argparse subcommands to functions in
``dexalot_sdk.utils.secrets_vault`` and handles env-driven configuration of
the vault path and encryption key. Tests below exercise each command end to
end against a real on-disk vault (using a freshly generated Fernet key) and
spot-check error paths with monkeypatched stubs.
"""

import argparse
import os
from unittest.mock import MagicMock, patch

import pytest

from dexalot_sdk.scripts import secrets_vault_cli as cli
from dexalot_sdk.utils.result import Result
from dexalot_sdk.utils.secrets_vault import generate_secrets_vault_key


@pytest.fixture
def vault_env(tmp_path, monkeypatch):
    """Point the CLI at a temp vault file and supply a real Fernet key.

    Yields a dict carrying the resolved ``vault_path`` and ``key`` so each
    test can drive the helpers directly without re-reading the environment.
    """
    vault_path = tmp_path / "vault.json"
    key = generate_secrets_vault_key()
    monkeypatch.setenv("DEXALOT_SECRETS_VAULT_PATH", str(vault_path))
    monkeypatch.setenv("DEXALOT_SECRETS_VAULT_KEY", key)
    yield {"vault_path": str(vault_path), "key": key}


# ---------------------------------------------------------------------------
# _load_dotenv
# ---------------------------------------------------------------------------


def test_load_dotenv_no_file(tmp_path, monkeypatch):
    """Absent .env is a silent no-op — no env vars touched."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOAD_DOTENV_TEST", raising=False)
    cli._load_dotenv()
    assert "LOAD_DOTENV_TEST" not in os.environ


def test_load_dotenv_parses_lines(tmp_path, monkeypatch):
    """Quoted values are unwrapped; comments and blank lines are ignored."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOAD_DOTENV_QUOTED", raising=False)
    monkeypatch.delenv("LOAD_DOTENV_PLAIN", raising=False)
    monkeypatch.delenv("LOAD_DOTENV_NO_EQUALS", raising=False)
    (tmp_path / ".env").write_text(
        "# a comment line\n"
        "\n"
        'LOAD_DOTENV_QUOTED="quoted-value"\n'
        "LOAD_DOTENV_PLAIN=plain\n"
        "MALFORMED_NO_EQUALS_SIGN\n"
    )
    cli._load_dotenv()
    assert os.environ["LOAD_DOTENV_QUOTED"] == "quoted-value"
    assert os.environ["LOAD_DOTENV_PLAIN"] == "plain"
    assert "LOAD_DOTENV_NO_EQUALS" not in os.environ


def test_load_dotenv_does_not_overwrite_existing(tmp_path, monkeypatch):
    """Pre-set env vars win over .env values."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LOAD_DOTENV_OVERRIDE=from-file\n")
    monkeypatch.setenv("LOAD_DOTENV_OVERRIDE", "from-shell")
    cli._load_dotenv()
    assert os.environ["LOAD_DOTENV_OVERRIDE"] == "from-shell"


def test_load_dotenv_swallows_oserror(tmp_path, monkeypatch):
    """An unreadable .env doesn't crash the CLI."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("X=1\n")
    with patch("pathlib.Path.open", side_effect=OSError("permission denied")):
        cli._load_dotenv()  # no exception


# ---------------------------------------------------------------------------
# _resolve_vault_path / _resolve_encryption_key
# ---------------------------------------------------------------------------


def test_resolve_vault_path_uses_env(monkeypatch):
    monkeypatch.setenv("DEXALOT_SECRETS_VAULT_PATH", "/tmp/custom.json")
    assert cli._resolve_vault_path() == "/tmp/custom.json"


def test_resolve_vault_path_default(monkeypatch):
    monkeypatch.delenv("DEXALOT_SECRETS_VAULT_PATH", raising=False)
    assert cli._resolve_vault_path() == os.path.expanduser("~/.dexalot/secrets_vault.json")


def test_resolve_encryption_key_from_env(monkeypatch):
    monkeypatch.setenv("DEXALOT_SECRETS_VAULT_KEY", "from-env-key")
    assert cli._resolve_encryption_key() == "from-env-key"


def test_resolve_encryption_key_from_prompt(monkeypatch):
    monkeypatch.delenv("DEXALOT_SECRETS_VAULT_KEY", raising=False)
    with patch.object(cli.getpass, "getpass", return_value="prompted-key"):
        assert cli._resolve_encryption_key() == "prompted-key"


def test_resolve_encryption_key_empty_prompt_exits(monkeypatch, capsys):
    monkeypatch.delenv("DEXALOT_SECRETS_VAULT_KEY", raising=False)
    with patch.object(cli.getpass, "getpass", return_value="   "):
        with pytest.raises(SystemExit) as exc_info:
            cli._resolve_encryption_key()
    assert exc_info.value.code == 1
    assert "must not be empty" in capsys.readouterr().err


def test_resolve_encryption_key_eof_exits(monkeypatch, capsys):
    monkeypatch.delenv("DEXALOT_SECRETS_VAULT_KEY", raising=False)
    with patch.object(cli.getpass, "getpass", side_effect=EOFError):
        with pytest.raises(SystemExit) as exc_info:
            cli._resolve_encryption_key()
    assert exc_info.value.code == 1
    assert "Aborted" in capsys.readouterr().err


def test_resolve_encryption_key_keyboard_interrupt_exits(monkeypatch):
    monkeypatch.delenv("DEXALOT_SECRETS_VAULT_KEY", raising=False)
    with patch.object(cli.getpass, "getpass", side_effect=KeyboardInterrupt):
        with pytest.raises(SystemExit) as exc_info:
            cli._resolve_encryption_key()
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# cmd_keygen
# ---------------------------------------------------------------------------


def test_cmd_keygen_prints_key(capsys):
    rc = cli.cmd_keygen(argparse.Namespace())
    assert rc == 0
    captured = capsys.readouterr()
    # The key is printed to stdout; safety guidance goes to stderr.
    assert captured.out.strip()
    assert "Store this key" in captured.err


# ---------------------------------------------------------------------------
# cmd_add / cmd_get / cmd_list / cmd_delete (real vault round-trip)
# ---------------------------------------------------------------------------


def test_cmd_add_get_list_delete_round_trip(vault_env, capsys):
    args_add = argparse.Namespace(key="API_KEY", value="s3cret-value")
    assert cli.cmd_add(args_add) == 0
    assert "Stored 'API_KEY'" in capsys.readouterr().out

    args_get = argparse.Namespace(key="API_KEY")
    assert cli.cmd_get(args_get) == 0
    assert "s3cret-value" in capsys.readouterr().out

    assert cli.cmd_list(argparse.Namespace()) == 0
    list_out = capsys.readouterr().out
    assert "API_KEY" in list_out
    assert vault_env["vault_path"] in list_out

    args_delete = argparse.Namespace(key="API_KEY")
    assert cli.cmd_delete(args_delete) == 0
    assert "Deleted 'API_KEY'" in capsys.readouterr().out


def test_cmd_list_empty_vault(vault_env, capsys):
    """Empty-vault path emits a distinctive message and still returns 0."""
    rc = cli.cmd_list(argparse.Namespace())
    assert rc == 0
    assert "No entries" in capsys.readouterr().out


def test_cmd_add_failure_path(vault_env, capsys):
    """A vault library failure surfaces as exit code 1 + stderr message."""
    with patch.object(cli, "secrets_vault_set", create=True):
        # The function is imported lazily inside cmd_add; patch via the
        # helper module instead.
        with patch(
            "dexalot_sdk.utils.secrets_vault.secrets_vault_set",
            return_value=Result.fail("disk full"),
        ):
            rc = cli.cmd_add(argparse.Namespace(key="K", value="V"))
    assert rc == 1
    assert "disk full" in capsys.readouterr().err


def test_cmd_get_failure_path(vault_env, capsys):
    with patch(
        "dexalot_sdk.utils.secrets_vault.secrets_vault_get",
        return_value=Result.fail("not found"),
    ):
        rc = cli.cmd_get(argparse.Namespace(key="MISSING"))
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_cmd_list_failure_path(vault_env, capsys):
    with patch(
        "dexalot_sdk.utils.secrets_vault.secrets_vault_list",
        return_value=Result.fail("corrupt vault"),
    ):
        rc = cli.cmd_list(argparse.Namespace())
    assert rc == 1
    assert "corrupt vault" in capsys.readouterr().err


def test_cmd_delete_failure_path(vault_env, capsys):
    with patch(
        "dexalot_sdk.utils.secrets_vault.secrets_vault_remove",
        return_value=Result.fail("no such key"),
    ):
        rc = cli.cmd_delete(argparse.Namespace(key="MISSING"))
    assert rc == 1
    assert "no such key" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# build_parser / main
# ---------------------------------------------------------------------------


def test_build_parser_dispatches_subcommands():
    parser = cli.build_parser()
    args = parser.parse_args(["keygen"])
    assert args.func is cli.cmd_keygen

    args = parser.parse_args(["add", "K", "V"])
    assert args.func is cli.cmd_add
    assert args.key == "K"
    assert args.value == "V"

    args = parser.parse_args(["get", "K"])
    assert args.func is cli.cmd_get

    args = parser.parse_args(["list"])
    assert args.func is cli.cmd_list

    args = parser.parse_args(["delete", "K"])
    assert args.func is cli.cmd_delete


def test_build_parser_requires_subcommand(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_main_dispatches_and_returns_exit_code(monkeypatch):
    """``main()`` should call ``args.func(args)`` and return its int verbatim."""
    fake_func = MagicMock(return_value=42)

    fake_args = argparse.Namespace(func=fake_func)
    fake_parser = MagicMock()
    fake_parser.parse_args.return_value = fake_args

    monkeypatch.setattr(cli, "_load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "build_parser", lambda: fake_parser)

    assert cli.main() == 42
    fake_func.assert_called_once_with(fake_args)
