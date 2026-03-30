#!/usr/bin/env python3
"""
Version management script.

Syncs version across VERSION file, pyproject.toml, and any src/**/__init__.py
files that contain a __version__ assignment.

Commands:
  get       Print the current version from VERSION
  set       Set a specific version across all files
  bump      Bump major / minor / patch
  validate  Check all files are in sync
  info      Show per-file version breakdown
"""

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# Files and the regex pattern used to find (and replace) their version string.
# Each pattern must capture exactly one group: the version number itself.
_STATIC_TARGETS: list[tuple[Path, str]] = [
    (PROJECT_ROOT / "pyproject.toml", r'(?m)^version = "([^"]+)"'),
]

_VERSION_INIT_PATTERN = r'__version__ = "([^"]+)"'


def _discover_targets() -> list[tuple[Path, str]]:
    """Return all (file, pattern) pairs that carry a version string."""
    targets = list(_STATIC_TARGETS)
    for init in sorted((PROJECT_ROOT / "src").rglob("__init__.py")):
        if re.search(_VERSION_INIT_PATTERN, init.read_text()):
            targets.append((init, _VERSION_INIT_PATTERN))
    return targets


def _valid(version: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+", version))


def _parse(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


class VersionManager:
    def __init__(self) -> None:
        self.version_file = PROJECT_ROOT / "VERSION"
        self.targets = _discover_targets()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self) -> str:
        if not self.version_file.exists():
            raise FileNotFoundError(f"VERSION file not found: {self.version_file}")
        v = self.version_file.read_text().strip()
        if not _valid(v):
            raise ValueError(f"Invalid version in VERSION file: {v!r}")
        return v

    def _read_file_version(self, path: Path, pattern: str) -> str | None:
        m = re.search(pattern, path.read_text())
        return m.group(1) if m else None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def set(self, new_version: str) -> None:
        """Write new_version to VERSION and all discovered targets."""
        if not _valid(new_version):
            raise ValueError(f"Invalid version format: {new_version!r}")

        self.version_file.write_text(f"{new_version}\n")
        print(f"  VERSION  -> {new_version}")

        for path, pattern in self.targets:
            if not path.exists():
                print(f"  WARNING  {path.relative_to(PROJECT_ROOT)}: file not found, skipping")
                continue
            original = path.read_text()

            def _replacer(m: re.Match[str], nv: str = new_version) -> str:
                return m.group(0).replace(m.group(1), nv)

            updated = re.sub(pattern, _replacer, original)
            if updated != original:
                path.write_text(updated)
                print(f"  updated  {path.relative_to(PROJECT_ROOT)}")
            else:
                print(f"  no-op    {path.relative_to(PROJECT_ROOT)}")

    # ------------------------------------------------------------------
    # Bump
    # ------------------------------------------------------------------

    def bump(self, part: str) -> str:
        """Return the bumped version string (does NOT write anything)."""
        major, minor, patch = _parse(self.get())
        match part:
            case "major":
                return f"{major + 1}.0.0"
            case "minor":
                return f"{major}.{minor + 1}.0"
            case "patch":
                return f"{major}.{minor}.{patch + 1}"
            case _:
                raise ValueError(f"Unknown bump type: {part!r}")

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    def validate(self) -> bool:
        expected = self.get()
        print(f"Expected version: {expected}")
        ok = True
        for path, pattern in self.targets:
            if not path.exists():
                print(f"  WARNING  {path.relative_to(PROJECT_ROOT)}: not found")
                continue
            found = self._read_file_version(path, pattern)
            rel = path.relative_to(PROJECT_ROOT)
            if found == expected:
                print(f"  OK       {rel}: {found}")
            else:
                label = found if found else "<not found>"
                print(f"  MISMATCH {rel}: {label} (expected {expected})")
                ok = False
        return ok

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def info(self) -> None:
        current = self.get()
        major, minor, patch = _parse(current)
        print(f"VERSION file : {current}  (major={major} minor={minor} patch={patch})")
        print("Tracked files:")
        for path, pattern in self.targets:
            rel = path.relative_to(PROJECT_ROOT)
            if path.exists():
                found = self._read_file_version(path, pattern) or "<not found>"
            else:
                found = "<file missing>"
            print(f"  {rel}: {found}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync version across VERSION, pyproject.toml, and __init__.py files"
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("get", help="Print current version")
    sub.add_parser("validate", help="Check all files are in sync")
    sub.add_parser("info", help="Show per-file version breakdown")

    bump_p = sub.add_parser("bump", help="Bump major/minor/patch")
    bump_p.add_argument("part", choices=["major", "minor", "patch"], nargs="?", default="patch")
    bump_p.add_argument("--dry-run", action="store_true", help="Print result without writing")

    set_p = sub.add_parser("set", help="Set an explicit version")
    set_p.add_argument("version", help="e.g. 1.2.3")
    set_p.add_argument("--dry-run", action="store_true", help="Print result without writing")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    try:
        mgr = VersionManager()

        match args.command:
            case "get":
                print(mgr.get())

            case "set":
                if not _valid(args.version):
                    print(f"Error: invalid version format: {args.version!r}", file=sys.stderr)
                    sys.exit(1)
                if args.dry_run:
                    print(f"Would set version to {args.version} (dry-run)")
                else:
                    mgr.set(args.version)
                    print(f"Version set to {args.version}")

            case "bump":
                new_version = mgr.bump(args.part)
                if args.dry_run:
                    print(f"Would bump {args.part}: {mgr.get()} -> {new_version} (dry-run)")
                else:
                    print(f"Bumping {args.part}: {mgr.get()} -> {new_version}")
                    mgr.set(new_version)

            case "validate":
                if mgr.validate():
                    print("All versions are in sync.")
                else:
                    print("Version mismatch detected.", file=sys.stderr)
                    sys.exit(1)

            case "info":
                mgr.info()

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
