import io
import os
import plistlib
from pathlib import Path

import pytest

from tests.cli.fake_launchctl import FakeLaunchctl
from wsp.cli import daemon_cmd, launchd
from wsp.cli.main import main


@pytest.fixture
def have_wspd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launchd.shutil, "which", lambda _n: "/opt/x/bin/wspd")


def test_install_fresh(wsp_home: Path, have_wspd: None) -> None:
    fake, out = FakeLaunchctl(), io.StringIO()
    assert daemon_cmd.install(fake, out) == 0
    assert fake.verbs == ["print", "bootstrap"]
    assert fake.calls[1] == ["bootstrap", f"gui/{os.getuid()}", str(launchd.plist_path())]
    assert launchd.plist_path().exists()
    assert "Instalado" in out.getvalue()


def test_install_replaces_existing(wsp_home: Path, have_wspd: None) -> None:
    fake = FakeLaunchctl(loaded=True)
    assert daemon_cmd.install(fake, io.StringIO()) == 0
    assert fake.verbs == ["print", "bootout", "bootstrap"]
    assert fake.loaded


def test_install_twice_ok(wsp_home: Path, have_wspd: None) -> None:
    fake = FakeLaunchctl()
    assert daemon_cmd.install(fake, io.StringIO()) == 0
    assert daemon_cmd.install(fake, io.StringIO()) == 0
    with launchd.plist_path().open("rb") as f:
        assert plistlib.load(f)["Label"] == launchd.LABEL


def test_install_without_wspd(
    wsp_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(launchd.shutil, "which", lambda _n: None)
    fake = FakeLaunchctl()
    assert daemon_cmd.install(fake, io.StringIO()) == 1
    assert fake.calls == []
    assert "wspd" in capsys.readouterr().err


def test_install_bootstrap_failure(
    wsp_home: Path, have_wspd: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert daemon_cmd.install(FakeLaunchctl(fail={"bootstrap": 5}), io.StringIO()) == 1
    assert "bootstrap falló" in capsys.readouterr().err


def test_uninstall_removes_plist_keeps_data(wsp_home: Path, have_wspd: None) -> None:
    fake = FakeLaunchctl()
    daemon_cmd.install(fake, io.StringIO())
    data = wsp_home / "data" / "wsp.db"
    data.write_bytes(b"x")
    assert daemon_cmd.uninstall(fake, io.StringIO()) == 0
    assert "bootout" in fake.verbs
    assert not launchd.plist_path().exists()
    assert data.exists()


def test_uninstall_when_absent_is_ok(wsp_home: Path) -> None:
    fake = FakeLaunchctl()
    assert daemon_cmd.uninstall(fake, io.StringIO()) == 0
    assert fake.verbs == ["print"]


def test_uninstall_bootout_failure_keeps_plist(wsp_home: Path, have_wspd: None) -> None:
    daemon_cmd.install(FakeLaunchctl(), io.StringIO())
    assert daemon_cmd.uninstall(FakeLaunchctl(loaded=True, fail={"bootout": 5}), io.StringIO()) == 1
    assert launchd.plist_path().exists()


def test_restart_uses_kickstart_k(wsp_home: Path) -> None:
    fake = FakeLaunchctl(loaded=True)
    assert daemon_cmd.restart(fake, io.StringIO()) == 0
    assert fake.calls[-1] == ["kickstart", "-k", launchd.service_target()]


def test_restart_not_installed(wsp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert daemon_cmd.restart(FakeLaunchctl(), io.StringIO()) == 1
    assert "install" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("00:07", 7), ("12:34", 754), ("01:02:03", 3723), ("2-03:04:05", 183845), ("basura", None)],
)
def test_parse_etime(text: str, seconds: int | None) -> None:
    assert daemon_cmd.parse_etime(text) == seconds


def test_format_uptime() -> None:
    assert daemon_cmd.format_uptime(5) == "5s"
    assert daemon_cmd.format_uptime(183845) == "2d 3h 4m"


def _status(fake: FakeLaunchctl, probe: tuple[bool, str | None]) -> tuple[int, str]:
    out = io.StringIO()
    code = daemon_cmd.status(fake, lambda: probe, lambda _pid: "01:02:03", out)
    return code, out.getvalue()


def test_status_not_loaded() -> None:
    code, text = _status(FakeLaunchctl(), (True, "connected"))
    assert code == 1
    assert "no cargado" in text


def test_status_connected() -> None:
    code, text = _status(FakeLaunchctl(loaded=True, pid=4242), (True, "connected"))
    assert code == 0
    assert "pid: 4242" in text
    assert "uptime: 1h 2m" in text
    assert "cuenta: connected" in text


def test_status_socket_unreachable() -> None:
    code, text = _status(FakeLaunchctl(loaded=True, pid=1), (False, None))
    assert code == 2
    assert "sin respuesta" in text


@pytest.mark.parametrize("account", ["logged_out", "disconnected", None])
def test_status_account_not_connected(account: str | None) -> None:
    code, _ = _status(FakeLaunchctl(loaded=True, pid=1), (True, account))
    assert code == 3


def test_status_loaded_without_pid() -> None:
    code, text = _status(FakeLaunchctl(loaded=True), (False, None))
    assert code == 2
    assert "pid: -" in text
    assert "uptime: -" in text


def test_cli_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon_cmd, "install", lambda: 7)
    assert main(["daemon", "install"]) == 7


def test_cli_requires_subcommand() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["daemon"])
    assert exc.value.code == 2
