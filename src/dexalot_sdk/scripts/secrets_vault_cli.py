#!/usr/bin/env python3
"""
CLI tool to manage the Dexalot secrets vault.

Reads DEXALOT_SECRETS_VAULT_PATH from .env (or the environment) to locate the
vault file. The encryption key is never stored - it must be supplied via the
DEXALOT_SECRETS_VAULT_KEY environment variable or entered interactively.

Usage:
    python -m dexalot_sdk.scripts.secrets_vault_cli <command> [args]

Commands:
    keygen              Generate and print a new Fernet encryption key.
    add <key> <value>   Encrypt and store (or overwrite) a key-value pair.
    get <key>           Retrieve and decrypt a value.
    list                List all stored key names (no decryption needed).
    delete <key>        Remove a key-value pair from the vault.

Environment variables:
    DEXALOT_SECRETS_VAULT_PATH   Path to the vault file
                                  (default: ~/.dexalot/secrets_vault.json).
    DEXALOT_SECRETS_VAULT_KEY    Encryption key - if not set, prompted interactively.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """Load .env from the current directory (if it exists) without overwriting existing env vars."""
    env_file = Path(".env")
    if not env_file.exists():
        return
    try:
        with env_file.open() as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


def _resolve_vault_path() -> str:
    default = os.path.expanduser("~/.dexalot/secrets_vault.json")
    return os.path.expanduser(os.environ.get("DEXALOT_SECRETS_VAULT_PATH", default))


def _resolve_encryption_key() -> str:
    """Return the encryption key from env or a secure terminal prompt."""
    key = os.environ.get("DEXALOT_SECRETS_VAULT_KEY", "").strip()
    if key:
        return key
    try:
        key = getpass.getpass("Enter secrets vault encryption key: ")
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
    if not key.strip():
        print("Error: encryption key must not be empty.", file=sys.stderr)
        sys.exit(1)
    return key.strip()


def cmd_keygen(_args: argparse.Namespace) -> int:
    from dexalot_sdk.utils.secrets_vault import generate_secrets_vault_key

    key = generate_secrets_vault_key()
    print(key)
    print(
        "\nStore this key in a safe place (e.g. a password manager).\n"
        "Set DEXALOT_SECRETS_VAULT_KEY=<key> before starting the MCP server,\n"
        "or enter it when prompted at startup.",
        file=sys.stderr,
    )
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    from dexalot_sdk.utils.secrets_vault import secrets_vault_set

    encryption_key = _resolve_encryption_key()
    vault_path = _resolve_vault_path()
    result = secrets_vault_set(vault_path, args.key, args.value, encryption_key)
    if not result.success:
        print(f"Error: {result.error}", file=sys.stderr)
        return 1
    print(f"Stored '{args.key}' in {vault_path}")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    from dexalot_sdk.utils.secrets_vault import secrets_vault_get

    encryption_key = _resolve_encryption_key()
    vault_path = _resolve_vault_path()
    result = secrets_vault_get(vault_path, args.key, encryption_key)
    if not result.success:
        print(f"Error: {result.error}", file=sys.stderr)
        return 1
    print(result.data)
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    from dexalot_sdk.utils.secrets_vault import secrets_vault_list

    vault_path = _resolve_vault_path()
    result = secrets_vault_list(vault_path)
    if not result.success:
        print(f"Error: {result.error}", file=sys.stderr)
        return 1
    if not result.data:
        print(f"No entries in {vault_path}")
        return 0
    print(f"Keys stored in {vault_path}:")
    for key in result.data:
        print(f"  {key}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    from dexalot_sdk.utils.secrets_vault import secrets_vault_remove

    vault_path = _resolve_vault_path()
    result = secrets_vault_remove(vault_path, args.key)
    if not result.success:
        print(f"Error: {result.error}", file=sys.stderr)
        return 1
    print(f"Deleted '{args.key}' from {vault_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Dexalot encrypted secrets vault.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("keygen", help="Generate a new encryption key.")
    p.set_defaults(func=cmd_keygen)

    p = sub.add_parser("add", help="Add or overwrite a key/value pair.")
    p.add_argument("key", help="Key name to store")
    p.add_argument("value", help="Secret value to encrypt and store")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("get", help="Retrieve and decrypt a value.")
    p.add_argument("key", help="Key name to retrieve")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("list", help="List stored key names.")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("delete", help="Delete a stored key/value pair.")
    p.add_argument("key", help="Key name to delete")
    p.set_defaults(func=cmd_delete)

    return parser


def main() -> int:
    _load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
