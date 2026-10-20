import pytest

from wsp import __version__
from wsp.cli.main import app
from wsp.daemon.main import run


def test_version() -> None:
    assert __version__


def test_entrypoints_run(capsys: pytest.CaptureFixture[str]) -> None:
    app()
    run()
    out = capsys.readouterr().out
    assert "wsp " in out
    assert "wspd " in out
