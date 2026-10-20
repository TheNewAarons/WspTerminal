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


def test_ensure_dirs_fixes_existing_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSP_HOME", str(tmp_path))
    paths.data_dir().mkdir(parents=True, mode=0o755)
    paths.data_dir().chmod(0o755)
    paths.ensure_dirs()
    paths.ensure_dirs()
    assert stat.S_IMODE(paths.data_dir().stat().st_mode) == 0o700


def test_ensure_dirs_rejects_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSP_HOME", str(tmp_path / "home"))
    target = tmp_path / "elsewhere"
    target.mkdir()
    paths.data_dir().parent.mkdir(parents=True)
    paths.data_dir().symlink_to(target)
    with pytest.raises(PermissionError):
        paths.ensure_dirs()


def test_ensure_dirs_rejects_long_socket_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WSP_HOME", str(tmp_path / ("x" * 100)))
    with pytest.raises(OSError, match="AF_UNIX"):
        paths.ensure_dirs()
