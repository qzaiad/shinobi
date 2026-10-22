"""Read KEY=VALUE pairs from a .env file, overridden by the process environment.

Services get their configuration from environment variables (compose sets them). Tools run from a
shell with `uv run`, which does not load .env, so they call `load_env()` to get the same values.
Deliberately minimal, like the parser in tests/test_shinobi.py: no quotes, no `export`, no
${VAR} expansion (our .env needs none).
"""

from __future__ import annotations

import os
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
