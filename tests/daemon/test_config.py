"""load_config: defaults, TOML válido y errores sin valores."""

from pathlib import Path

import pytest

from wsp.daemon.config import Config, ConfigError, load_config


def write(tmp_path: Path, text: str | bytes) -> Path:
    p = tmp_path / "config.toml"
    if isinstance(text, bytes):
        p.write_bytes(text)
    else:
        p.write_text(text)
    return p


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg == Config()
    assert (cfg.outbox.rate_per_sec, cfg.outbox.burst, cfg.outbox.max_attempts) == (1.0, 5, 3)
    assert (cfg.reconnect.base, cfg.reconnect.cap, cfg.reconnect.jitter) == (1.0, 300.0, 0.3)
    assert (cfg.log.level, cfg.retention_days) == ("INFO", 365)


def test_default_path_follows_wsp_home(short_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSP_HOME", str(short_path))
    (short_path / "config").mkdir()
    (short_path / "config" / "config.toml").write_text("retention_days = 30\n")
    assert load_config().retention_days == 30


def test_valid_toml_overrides(tmp_path: Path) -> None:
    p = write(
        tmp_path,
        'retention_days = 10\n[outbox]\nburst = 2\nrate_per_sec = 0.5\n[log]\nlevel = "DEBUG"\n'
        "[reconnect]\ncap = 60\n",
    )
    cfg = load_config(p)
    assert (cfg.outbox.burst, cfg.outbox.rate_per_sec) == (2, 0.5)
    assert (cfg.log.level, cfg.retention_days, cfg.reconnect.cap) == ("DEBUG", 10, 60.0)
    assert cfg.outbox.max_attempts == 3


def test_empty_file_gives_defaults(tmp_path: Path) -> None:
    assert load_config(write(tmp_path, "")) == Config()


@pytest.mark.parametrize(
    "text",
    [
        'clave_rara = "hunter2"\n',
        '[outbox]\nsecreto = "hunter2"\n',
        '[seccion]\nx = "hunter2"\n',
        '[log]\nlevel = "hunter2"\n',
        '[outbox]\nburst = "hunter2"\n',
        "[outbox]\nrate_per_sec = 99999\n",
        'password = "hunter2\n',
        "[[[",
    ],
)
def test_invalid_config_error_has_no_values(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError) as e:
        load_config(write(tmp_path, text))
    msg = str(e.value)
    assert "hunter2" not in msg and "99999" not in msg


def test_invalid_names_the_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"outbox\.burst"):
        load_config(write(tmp_path, "[outbox]\nburst = 0\n"))


def test_invalid_utf8(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, b"\xff\xfe\x00bad"))


def test_unreadable_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path)  # es un directorio


def test_config_is_frozen() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        Config().retention_days = 5
