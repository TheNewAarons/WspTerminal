from collections.abc import Mapping
from pathlib import Path
from typing import Any

from wsp import paths
from wsp.cli import daemon_cmd
from wsp.protocol.models import AccountState, HandledCommand
from wsp.protocol.server import Server


async def _handler(_rid: str, _cmd: HandledCommand) -> Mapping[str, Any]:
    return {}


async def _no_backfill(_since: int) -> tuple[list[Any], bool]:
    return [], False


async def test_probe_no_daemon(wsp_home: Path) -> None:
    assert await daemon_cmd.probe_socket(timeout=0.3) == (False, None)


async def test_probe_reports_account_state(wsp_home: Path) -> None:
    server = Server(paths.socket_path(), _handler, _no_backfill)
    await server.start()
    try:
        server.broadcast(AccountState(state="connected"))
        assert await daemon_cmd.probe_socket(timeout=3.0) == (True, "connected")
    finally:
        await server.stop()


async def test_probe_without_account_state(wsp_home: Path) -> None:
    server = Server(paths.socket_path(), _handler, _no_backfill)
    await server.start()
    try:
        assert await daemon_cmd.probe_socket(timeout=3.0) == (True, None)
    finally:
        await server.stop()
