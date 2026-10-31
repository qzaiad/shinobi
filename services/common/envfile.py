"""Read KEY=VALUE pairs from a .env file (overridden by the process environment); update them.

Services get their configuration from environment variables (compose sets them). Tools run from a
shell with `uv run`, which does not load .env, so they call `load_env()` to get the same values.
Deliberately minimal, like the parser in tests/test_shinobi.py: no quotes, no `export`, no
${VAR} expansion (our .env needs none).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def load_env(path: Path = Path(".env")) -> dict[str, str]:
    """Return .env values (if the file exists) with os.environ on top: real env vars win."""
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")  # split at the FIRST '=' only
            if sep and not key.lstrip().startswith("#"):
                values[key.strip()] = value.strip()
    values.update(os.environ)
    return values


def update_env(path: Path, updates: Mapping[str, str]) -> list[str]:
    """Set KEY=VALUE lines in a .env file in place; return the keys whose value changed.

    The first uncommented `KEY=` line is rewritten, everything else (comments, order, blank
    lines, other keys) stays byte-identical; keys not in the file are appended. Written to a
    temporary file in the same directory and renamed over the original (atomic: a crash leaves
    the old or the new file, never half of one), with mode 0600 since it holds credentials.
    """
    for key, value in updates.items():
        if not key or "=" in key or "\n" in value or "\n" in key:
            raise ValueError(f"cannot store {key!r} in a .env file")
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    changed: list[str] = []
    seen: set[str] = set()
    for i, line in enumerate(lines):
        key, sep, old = line.partition("=")
        key = key.strip()
        if sep and not key.startswith("#") and key in updates and key not in seen:
            seen.add(key)
            if old.strip() != updates[key]:
                lines[i] = f"{key}={updates[key]}"
                changed.append(key)
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")
            changed.append(key)
    if changed:
        tmp = path.with_name(f".{path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    elif path.exists():
        os.chmod(path, 0o600)  # tighten an existing file even when nothing changed
    return changed
