"""Where the project keeps its data, and the settings a user can change."""

from __future__ import annotations

import os
from pathlib import Path

# src/better/config.py -> the project root is two levels above the package directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Settings read by `setting()`. Each can be given in the environment or in `.env`.
ODDS_API_KEY = "ODDS_API_KEY"                   # The Odds API key; without it that source is off
PRIZEPICKS_DIRECT = "BETTER_PRIZEPICKS_DIRECT"  # "yes" allows calling PrizePicks itself
MIRROR = "BETTER_MIRROR"                        # "no" skips the PrizePicks mirror

_TRUE = {"1", "yes", "true", "on"}
_FALSE = {"0", "no", "false", "off"}


def data_dir() -> Path:
    """Directory for downloaded files and the database.

    Defaults to ``<project>/data``. Set ``BETTER_DATA_DIR`` to keep it somewhere else.
    """
    override = os.environ.get("BETTER_DATA_DIR")
    return Path(override).expanduser() if override else PROJECT_ROOT / "data"


def db_path() -> Path:
    return data_dir() / "better.duckdb"


def raw_dir() -> Path:
    """Downloaded source files, kept so the database can be rebuilt without the network."""
    return data_dir() / "raw"


def env_file() -> Path:
    """The file settings are read from: ``.env`` in the project root, ignored by git."""
    override = os.environ.get("BETTER_ENV_FILE")
    return Path(override).expanduser() if override else PROJECT_ROOT / ".env"


def read_env_file(path: Path) -> dict[str, str]:
    """``KEY=VALUE`` lines of a file. Blank lines and ``#`` comments are skipped."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def setting(name: str) -> str | None:
    """One setting, from the environment first and ``.env`` second. Empty means unset."""
    value = os.environ.get(name)
    if value is None:
        value = read_env_file(env_file()).get(name)
    value = (value or "").strip()
    return value or None


def flag(name: str, default: bool) -> bool:
    """A yes/no setting. Anything that is not clearly yes or no leaves the default."""
    value = (setting(name) or "").lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default
