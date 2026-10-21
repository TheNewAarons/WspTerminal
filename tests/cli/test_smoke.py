import pytest

from wsp import __version__
from wsp.cli.main import app, main
from wsp.cli.render import Palette


def test_version() -> None:
    assert __version__


def test_main_without_command_opens_repl(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    async def fake_run(*, pal: Palette) -> int:
        seen.append(pal.enabled)
        return 0

    monkeypatch.setattr("wsp.cli.repl.run", fake_run)
    assert main(["--no-color"]) == 0
    assert seen == [False]


def test_app_exits_with_main_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["wsp", "--version"])
    with pytest.raises(SystemExit) as exc:
        app()
    assert exc.value.code == 0
