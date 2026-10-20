from wsp import __version__
from wsp.cli.main import main as cli_main
from wsp.daemon.main import main as daemon_main


def test_version() -> None:
    assert __version__


def test_entrypoints_run(capsys: object) -> None:
    cli_main()
    daemon_main()
