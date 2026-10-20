"""wspd.serve y main(): arranque, persistencia, apagado limpio, lock tomado."""

import asyncio
import sqlite3
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.daemon.fakes import DIRECT_STR, FakeClient, message_ev, text_msg
from wsp import paths
from wsp.daemon import main as dmain
from wsp.daemon.config import Config
from wsp.daemon.session import EXIT_ALREADY_RUNNING, InstanceLock, WaClient
from wsp.protocol.models import MAX_LINE, Envelope, decode_line, encode, read_line
from wsp.store import db, repo

pytestmark = pytest.mark.usefixtures("restore_process_state")


async def _until(pred: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not pred():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def test_serve_persists_incoming_and_shuts_down_cleanly(
    wsp_home: Path, unix_sockets: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    conns: list[sqlite3.Connection] = []
    real_open = db.open_write

    def capture() -> sqlite3.Connection:
        c = real_open()
        conns.append(c)
        return c

    monkeypatch.setattr("wsp.daemon.main.db.open_write", capture)
    stop = asyncio.Event()

    def factory() -> WaClient:
        return client

    task = asyncio.create_task(dmain.serve(Config(), factory, stop))
    sock = paths.socket_path()
    await _until(lambda: sock.exists() and client.connects >= 1 and client.on_event is not None)
    assert stat.S_IMODE(sock.stat().st_mode) == 0o600

    reader, writer = await asyncio.open_unix_connection(str(sock), limit=MAX_LINE + 1)
    try:
        hello = Envelope(type="hello", request_id="h1", data={"client": "cli"})
        writer.write(encode(hello))
        await writer.drain()
        first = await asyncio.wait_for(read_line(reader), 5)
        assert first is not None  # respuesta al hello

        await client.emit("MessageEv", message_ev(text_msg("hola daemon"), wa_id="IN1"))

        seen: list[str] = []
        async with asyncio.timeout(5):
            while "message.new" not in seen:
                line = await read_line(reader)
                assert line is not None
                seen.append(decode_line(line).type)
    finally:
        writer.close()

    ro = db.open_read()
    try:
        rows = repo.last_messages(ro, DIRECT_STR)
    finally:
        ro.close()
    assert [(r.wa_id, r.body) for r in rows] == [("IN1", "hola daemon")]

    stop.set()
    await asyncio.wait_for(task, 10)
    assert not sock.exists()
    assert client.disconnects >= 1
    assert len(conns) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        conns[0].execute("SELECT 1")


async def test_serve_exits_when_logged_out(wsp_home: Path, unix_sockets: None) -> None:
    client = FakeClient()
    task = asyncio.create_task(dmain.serve(Config(), lambda: client, asyncio.Event()))
    await _until(lambda: client.connects >= 1 and client.on_event is not None)
    await client.emit("LoggedOutEv")
    client.close()
    await asyncio.wait_for(task, 10)
    assert not paths.socket_path().exists()


def test_main_with_lock_held_returns_2(
    wsp_home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(*_a: object, **_k: object) -> None:
        raise AssertionError("no debe arrancar serve")

    monkeypatch.setattr(dmain, "serve", never)
    with InstanceLock():
        assert dmain.main() == EXIT_ALREADY_RUNNING == 2
    assert capsys.readouterr().err.startswith("wspd:")


def test_main_invalid_config_returns_1(wsp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths.config_path().write_text('oculto = "hunter2"\n')
    assert dmain.main() == 1
    err = capsys.readouterr().err
    assert "wspd:" in err and "hunter2" not in err


def test_main_runs_serve_and_releases_lock(wsp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Config] = []

    async def fake_serve(cfg: Config) -> None:
        called.append(cfg)

    monkeypatch.setattr(dmain, "serve", fake_serve)
    assert dmain.main() == 0
    assert len(called) == 1
    assert (paths.state_dir() / "wspd.log").exists()
    with InstanceLock():  # el lock quedó liberado
        pass


def test_main_releases_lock_if_serve_crashes(
    wsp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(cfg: Config) -> None:
        raise RuntimeError("x")

    monkeypatch.setattr(dmain, "serve", boom)
    with pytest.raises(RuntimeError):
        dmain.main()
    with InstanceLock():
        pass


def test_run_exits_with_main_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dmain, "main", lambda: 7)
    with pytest.raises(SystemExit) as e:
        dmain.run()
    assert e.value.code == 7
