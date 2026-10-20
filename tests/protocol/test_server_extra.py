import asyncio
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.protocol.conftest import CHAT, FakeBackfill, HandlerBox, Raw, until
from wsp.protocol.models import HandledCommand, MessageNew
from wsp.protocol.server import CommandError, Server, sqlite_backfill

MakeServer = Callable[..., Awaitable[Server]]
Connect = Callable[[], Awaitable[Raw]]

TEXT = {"chat_jid": CHAT, "text": "hola"}


def _unlink(path: Path) -> None:
    path.unlink()


def _exists(path: Path) -> bool:
    return os.path.exists(path)


async def _ready(connect: Connect) -> Raw:
    raw = await connect()
    env = await raw.hello()
    assert env.type == "ok"
    return raw


# ---- stop() / A1 ---------------------------------------------------------------------------


async def test_stop_does_not_remove_foreign_socket(
    make_server: MakeServer, sock_path: Path
) -> None:
    server = await make_server()
    _unlink(sock_path)  # otro daemon toma la ruta
    other = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        other.bind(str(sock_path))
        await server.stop()
        assert _exists(sock_path)
    finally:
        other.close()


async def test_stop_removes_own_socket(make_server: MakeServer, sock_path: Path) -> None:
    server = await make_server()
    await server.stop()
    assert not _exists(sock_path)


# ---- idempotencia / A3 ---------------------------------------------------------------------


async def test_same_request_id_runs_handler_once(
    make_server: MakeServer, connect: Connect, handler: HandlerBox
) -> None:
    await make_server()
    raw = await _ready(connect)
    await raw.send("send_text", "r1", TEXT)
    await raw.send("send_text", "r1", TEXT)
    a, b = await raw.recv_n(2)
    assert a.type == b.type == "ok"
    assert a.data == b.data
    assert handler.seen == ["r1"]


async def test_same_request_id_other_content_fails(
    make_server: MakeServer, connect: Connect, handler: HandlerBox
) -> None:
    await make_server()
    raw = await _ready(connect)
    await raw.send("send_text", "r1", TEXT)
    await raw.send("send_text", "r1", {"chat_jid": CHAT, "text": "otro"})
    a, b = await raw.recv_n(2)
    assert a.type == "ok"
    assert b.type == "fail"
    assert b.data["code"] == "request_id_reused"
    assert handler.seen == ["r1"]


async def test_in_flight_duplicate_shares_result(
    make_server: MakeServer, connect: Connect, handler: HandlerBox
) -> None:
    await make_server()
    gate = asyncio.Event()

    async def slow(rid: str, cmd: HandledCommand) -> dict[str, Any]:
        await gate.wait()
        return {"n": 1}

    handler.fn = slow
    r1, r2 = await _ready(connect), await _ready(connect)
    await r1.send("send_text", "r1", TEXT)
    await until(lambda: handler.seen == ["r1"])
    await r2.send("send_text", "r1", TEXT)
    await asyncio.sleep(0.05)
    gate.set()
    a, b = await r1.recv(), await r2.recv()
    assert a is not None
    assert b is not None
    assert (a.type, a.data) == (b.type, b.data) == ("ok", {"n": 1})
    assert handler.seen == ["r1"]


async def test_command_error_is_cached(
    make_server: MakeServer, connect: Connect, handler: HandlerBox
) -> None:
    await make_server()

    async def deny(rid: str, cmd: HandledCommand) -> dict[str, Any]:
        raise CommandError("nope", "no")

    handler.fn = deny
    raw = await _ready(connect)
    await raw.send("send_text", "r1", TEXT)
    await raw.send("send_text", "r1", TEXT)
    a, b = await raw.recv_n(2)
    assert a.data["code"] == b.data["code"] == "nope"
    assert handler.seen == ["r1"]


# ---- timeouts / límites --------------------------------------------------------------------


async def test_hello_timeout(make_server: MakeServer, connect: Connect) -> None:
    await make_server(hello_timeout=0.1)
    raw = await connect()
    env = await raw.recv()
    assert env is not None
    assert env.data["code"] == "hello_timeout"
    assert await raw.recv() is None


async def test_max_conns(
    make_server: MakeServer, connect: Connect, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("wsp.protocol.server._MAX_CONNS", 2)
    await make_server()
    await _ready(connect)
    await _ready(connect)
    third = await connect()
    await third.send("ping", "p1")
    try:
        env = await third.recv()
    except ConnectionError:
        env = None
    assert env is None


async def test_handler_timeout_not_cached(
    make_server: MakeServer, connect: Connect, handler: HandlerBox
) -> None:
    await make_server(handler_timeout=0.1)

    async def hang(rid: str, cmd: HandledCommand) -> dict[str, Any]:
        await asyncio.sleep(10)
        return {}

    handler.fn = hang
    raw = await _ready(connect)
    await raw.send("send_text", "r1", TEXT)
    env = await raw.recv()
    assert env is not None
    assert env.type == "fail"
    assert env.data["code"] == "timeout"

    async def fast(rid: str, cmd: HandledCommand) -> dict[str, Any]:
        return {"done": True}

    handler.fn = fast
    await raw.send("send_text", "r1", TEXT)  # mismo id: se puede reintentar
    env = await raw.recv()
    assert env is not None
    assert env.type == "ok"
    assert handler.seen == ["r1", "r1"]


async def test_untrusted_peer_is_rejected(
    make_server: MakeServer, connect: Connect, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("wsp.protocol.server.peer_uid", lambda _s: os.getuid() + 1)
    await make_server()
    raw = await connect()
    try:
        await raw.send("hello", "h1", {"client": "cli"})
        env = await raw.recv()
    except ConnectionError:
        env = None
    assert env is None


# ---- backfill ------------------------------------------------------------------------------


async def test_backfill_failed(
    sock_path: Path,
    handler: HandlerBox,
    connect: Connect,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def boom(since: int) -> tuple[list[MessageNew], bool]:
        raise RuntimeError("secreto-123")

    caplog.set_level(logging.DEBUG)
    server = Server(sock_path, handler, boom)
    await server.start()
    try:
        raw = await connect()
        assert (await raw.hello(since=0)).type == "ok"
        env = await raw.recv()
        assert env is not None
        assert env.data["code"] == "backfill_failed"
        assert await raw.recv() is None
    finally:
        await server.stop()
    assert "secreto-123" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)


async def test_sqlite_backfill_skips_invalid_rows(
    store_db: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def row(wa_id: str, chat_jid: str) -> SimpleNamespace:
        return SimpleNamespace(
            chat_jid=chat_jid,
            wa_id=wa_id,
            sender_jid=None,
            from_me=False,
            ts=5,
            kind="text",
            body="x",
            quoted_wa_id=None,
            status="sent",
            error=None,
            edited_at=None,
            deleted_at=None,
        )

    rows = [row("ok", CHAT), row("bad", "")]
    monkeypatch.setattr("wsp.store.repo.messages_since", lambda *_a, **_k: rows)
    caplog.set_level(logging.WARNING)
    events, truncated = await sqlite_backfill(store_db)(0)
    assert [e.wa_id for e in events] == ["ok"]
    assert truncated is False
    assert "n=1" in caplog.text


# ---- logs ----------------------------------------------------------------------------------


async def test_handler_failure_log_has_no_traceback_or_content(
    make_server: MakeServer,
    connect: Connect,
    handler: HandlerBox,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await make_server()

    async def boom(rid: str, cmd: HandledCommand) -> dict[str, Any]:
        raise RuntimeError("contenido-secreto")

    handler.fn = boom
    caplog.set_level(logging.DEBUG)
    raw = await _ready(connect)
    await raw.send("send_text", "r1", TEXT)
    env = await raw.recv()
    assert env is not None
    assert env.data["code"] == "internal"
    assert "contenido-secreto" not in caplog.text
    assert "Traceback" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


async def test_unused_fixture_types(backfill: FakeBackfill) -> None:
    assert backfill.calls == []
