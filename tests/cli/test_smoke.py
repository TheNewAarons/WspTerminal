import pytest

from wsp import __version__
from wsp.cli.main import app, main


def test_version() -> None:
    assert __version__


def test_main_without_command_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "wsp " in capsys.readouterr().out


def test_app_exits_with_main_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["wsp"])
    with pytest.raises(SystemExit) as exc:
        app()
    assert exc.value.code == 0
