"""Load and validate `.warmtree.toml`.

Every key has a default. An empty file, or no file at all, means a pool of
two slots parked on the repo's default branch with nothing to warm.
"""

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = ".warmtree.toml"


class ConfigError(Exception):
    """The config file is malformed or holds a value we cannot use."""


@dataclass(frozen=True)
class Config:
    size: int = 2
    base: str | None = None  # None means "detect the repo's default branch"
    dir: str = "../.warmtree"
    lockfiles: tuple[str, ...] = ()
    run: tuple[str, ...] = ()
    copy: tuple[str, ...] = ()
    env: bool = True


# Which keys live in which table, and the TOML type each must have.
# Anything not listed here is a typo, and typos are errors rather than silent
# defaults so a misspelled `run` never quietly skips warming.
_SCHEMA: dict[str, dict[str, type]] = {
    "pool": {"size": int, "base": str, "dir": str, "lockfiles": list},
    "warm": {"run": list, "copy": list, "env": bool},
}


def parse(text: str) -> Config:
    """Turn TOML text into a Config, or raise ConfigError."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc

    values: dict[str, object] = {}
    for table_name, table in data.items():
        if table_name not in _SCHEMA or not isinstance(table, dict):
            raise ConfigError(f"unknown table or key {table_name!r}")
        for key, value in table.items():
            expected = _SCHEMA[table_name].get(key)
            if expected is None:
                raise ConfigError(f"unknown key {key!r} in [{table_name}]")
            values[key] = _check(table_name, key, value, expected)

    size = values.get("size", Config.size)
    if isinstance(size, int) and size < 0:
        raise ConfigError("[pool] size must be zero or more")
    return Config(**values)  # type: ignore[arg-type]


def _check(table: str, key: str, value: object, expected: type) -> object:
    """Validate one value against its expected type. Lists become tuples."""
    # bool is a subclass of int in Python, so `size = true` needs its own check.
    if expected is int and isinstance(value, bool):
        raise ConfigError(f"[{table}] {key} must be an integer")
    if not isinstance(value, expected):
        raise ConfigError(f"[{table}] {key} must be of type {expected.__name__}")
    if expected is list:
        if not all(isinstance(item, str) for item in value):
            raise ConfigError(f"[{table}] {key} must be a list of strings")
        return tuple(value)
    return value


def load(repo_root: Path) -> Config:
    """Read the config at the repo root. A missing file means defaults."""
    path = repo_root / CONFIG_NAME
    if not path.exists():
        return Config()
    return parse(path.read_text(encoding="utf-8"))


def pool_dir(repo_root: Path, config: Config) -> Path:
    """Absolute path of the directory that holds the slots."""
    return (repo_root / config.dir).resolve()


def starter_toml(lockfiles: list[str]) -> str:
    """The file `warmtree init` writes. Defaults for everything except
    `lockfiles`, which is pre-filled with what init found in the repo.
    `run` is never guessed: warmtree does not know what a project needs.
    """
    # json.dumps of a list of strings is valid TOML for an array of strings.
    lockfiles_toml = json.dumps(lockfiles)
    return f"""\
# warmtree config. Every key has a default; delete a line to use it.

[pool]
size = 2                     # slots to keep ready
# base = "main"              # branch slots park on; default: repo default branch
dir = "../.warmtree"         # where slots live, relative to the repo root
lockfiles = {lockfiles_toml}  # re-warm a slot only when one of these changes

[warm]
run = []                     # run inside a slot at fill and refresh, e.g. ["npm ci"]
copy = []                    # untracked files copied from the main repo, e.g. [".env"]
env = true                   # write WARMTREE_SLOT=<n> into the copied env files
"""
