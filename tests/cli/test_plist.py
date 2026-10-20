import plistlib
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from wsp import paths
from wsp.cli import launchd


@pytest.fixture
def wspd(wsp_home: Path) -> Path:
    return Path("/opt/x/bin/wspd")


def test_plist_content(wsp_home: Path, wspd: Path) -> None:
    target = launchd.write_plist(wspd)
    assert target == wsp_home / "LaunchAgents" / "cl.aaron.wspd.plist"
    with target.open("rb") as f:
        data = plistlib.load(f)
    assert data["Label"] == "cl.aaron.wspd"
    assert data["ProgramArguments"] == ["/opt/x/bin/wspd"]
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] == {"SuccessfulExit": False}
    assert data["ThrottleInterval"] == 10
    assert data["StandardOutPath"] == str(paths.state_dir() / "wspd.stdout.log")
    assert data["StandardErrorPath"] == str(paths.state_dir() / "wspd.stderr.log")
    env = data["EnvironmentVariables"]
    assert env["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
    assert env["WSP_HOME"] == str(wsp_home)


def test_plist_without_wsp_home_has_minimal_env(
    monkeypatch: pytest.MonkeyPatch, wspd: Path
) -> None:
    monkeypatch.delenv("WSP_HOME", raising=False)
    assert launchd.build_plist(wspd)["EnvironmentVariables"] == {"PATH": launchd.MINIMAL_PATH}


def test_files_are_private(wsp_home: Path, wspd: Path) -> None:
    target = launchd.write_plist(wspd)
    for p in (target, paths.state_dir() / "wspd.stdout.log", paths.state_dir() / "wspd.stderr.log"):
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_rewrite_is_idempotent(wsp_home: Path, wspd: Path) -> None:
    launchd.write_plist(wspd)
    launchd.write_plist(Path("/other/wspd"))
    with launchd.plist_path().open("rb") as f:
        assert plistlib.load(f)["ProgramArguments"] == ["/other/wspd"]
    assert not list(launchd.plist_path().parent.glob("*.tmp"))


def test_symlink_plist_rejected(wsp_home: Path, wspd: Path) -> None:
    launchd.plist_path().parent.mkdir(parents=True)
    launchd.plist_path().symlink_to(wsp_home / "elsewhere")
    with pytest.raises(PermissionError):
        launchd.write_plist(wspd)


def test_resolve_wspd_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/local/bin/wspd")
    assert launchd.resolve_wspd() == Path("/usr/local/bin/wspd")


def test_resolve_wspd_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    with pytest.raises(launchd.WspdNotFoundError, match="uv tool install"):
        launchd.resolve_wspd()


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil solo en macOS")
def test_plutil_lint(wsp_home: Path, wspd: Path) -> None:
    target = launchd.write_plist(wspd)
    r = subprocess.run(["plutil", "-lint", str(target)], capture_output=True, check=False)  # noqa: S603,S607
    assert r.returncode == 0, r.stdout
