from pathlib import Path

import pytest

from warmtree import config
from warmtree.config import Config, ConfigError

FULL_EXAMPLE = """
[pool]
size = 3
base = "main"
dir = "../.warmtree"
lockfiles = ["package-lock.json", "uv.lock"]

[warm]
run = ["npm ci"]
copy = [".env", ".env.local"]
env = true
"""


def test_empty_text_gives_defaults():
    assert config.parse("") == Config()


def test_defaults_are_pool_of_two_on_detected_base():
    cfg = Config()
    assert cfg.size == 2
    assert cfg.base is None
    assert cfg.dir is None
    assert cfg.lockfiles == ()
    assert cfg.run == ()
    assert cfg.copy == ()
    assert cfg.env is True


def test_full_example_parses():
    cfg = config.parse(FULL_EXAMPLE)
    assert cfg.size == 3
    assert cfg.base == "main"
    assert cfg.lockfiles == ("package-lock.json", "uv.lock")
    assert cfg.run == ("npm ci",)
    assert cfg.copy == (".env", ".env.local")
    assert cfg.env is True


def test_unknown_key_is_an_error():
    with pytest.raises(ConfigError, match="sizes"):
        config.parse("[pool]\nsizes = 3\n")


def test_unknown_table_is_an_error():
    with pytest.raises(ConfigError, match="pools"):
        config.parse("[pools]\nsize = 3\n")


def test_wrong_type_is_an_error():
    with pytest.raises(ConfigError, match="size"):
        config.parse('[pool]\nsize = "3"\n')


def test_negative_size_is_an_error():
    with pytest.raises(ConfigError, match="size"):
        config.parse("[pool]\nsize = -1\n")


def test_invalid_toml_is_a_config_error():
    with pytest.raises(ConfigError):
        config.parse("[pool\n")


def test_load_missing_file_gives_defaults(tmp_path: Path):
    assert config.load(tmp_path) == Config()


def test_load_reads_file_at_repo_root(tmp_path: Path):
    (tmp_path / config.CONFIG_NAME).write_text("[pool]\nsize = 5\n")
    assert config.load(tmp_path).size == 5


def test_default_pool_dir_is_named_after_the_repo(tmp_path: Path):
    repo_root = tmp_path / "myrepo"
    repo_root.mkdir()
    expected = (tmp_path / ".warmtree" / "myrepo").resolve()
    assert config.pool_dir(repo_root, Config()) == expected


def test_explicit_pool_dir_is_relative_to_repo_root(tmp_path: Path):
    repo_root = tmp_path / "myrepo"
    repo_root.mkdir()
    cfg = Config(dir="../pools/x")
    assert config.pool_dir(repo_root, cfg) == (tmp_path / "pools" / "x").resolve()


def test_starter_toml_lists_lockfiles_and_never_guesses_run():
    text = config.starter_toml(["uv.lock"])
    cfg = config.parse(text)
    assert cfg.lockfiles == ("uv.lock",)
    assert cfg.run == ()


def test_starter_toml_round_trips_to_defaults():
    assert config.parse(config.starter_toml([])) == Config()
