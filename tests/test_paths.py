import stat
from pathlib import Path

import pytest

from wsp import paths


def test_wsp_home_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSP_HOME", str(tmp_path))
    assert paths.data_dir().is_relative_to(tmp_path)
    assert paths.socket_path().parent == paths.state_dir()
    assert paths.config_path().parent == paths.config_dir()


def test_default_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WSP_HOME", raising=False)
    assert paths.data_dir() == Path.home() / ".local" / "share" / "wsp"
    assert paths.state_dir() == Path.home() / ".local" / "state" / "wsp"
    assert paths.config_dir() == Path.home() / ".config" / "wsp"


def test_ensure_dirs_mode_700(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSP_HOME", str(tmp_path))
    paths.ensure_dirs()
    for d in (paths.data_dir(), paths.state_dir(), paths.config_dir()):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
