import asyncio
import os
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from tests.protocol.conftest import TIMEOUT, FakeBackfill, msg, next_event, until
from wsp.protocol.client import Client
from wsp.protocol.models import BackfillEnd, BackfillStart, MessageNew
from wsp.protocol.server import Server

MakeServer = Callable[..., Awaitable[Server]]
MakeClient = Callable[..., Client]


def _write(path: Path) -> None:
    path.write_text("no soy un socket")


async def test_truncated_backfill_reconnects_immediately(
    make_server: MakeServer, make_client: MakeClient, backfill: FakeBackfill
) -> None:
    await make_server()
    backfill.events = [msg("1", 10), msg("2", 20)]
    backfill.truncated = True
    sleeps: list[float] = []

    async def blocked_sleep(delay: float) -> None:
        sleeps.append(delay)
        await asyncio.Event().wait()

    client = make_client(since=0, sleep=blocked_sleep)
    await client.start()
    await until(lambda: len(sleeps) == 1)
    # La 1ª sesión avanzó (0 → 20) y reconectó sin dormir; la 2ª no avanza y duerme.
    assert backfill.calls == [0, 20]
    assert client.last_ts == 20


async def test_full_queue_reconnects(
    make_server: MakeServer, make_client: MakeClient, backfill: FakeBackfill, delays: list[float]
) -> None:
    server = await make_server()
    client = make_client(since=0, queue_size=2)
    await client.start()
    await client.wait_connected(TIMEOUT)
    await until(lambda: client._events.full())  # start + end del backfill llenan la cola
    server.broadcast(msg("1", 10))
    await until(lambda: len(backfill.calls) >= 2)
    assert delays
    assert client.last_ts == 0  # el evento descartado no avanza el cursor


async def test_identical_duplicate_dropped_status_change_delivered(
    make_server: MakeServer, make_client: MakeClient
) -> None:
    server = await make_server()
    client = make_client()
    await client.start()
    await client.wait_connected(TIMEOUT)
    it = client.events()
    first = msg("1", 10)
    server.broadcast(first)
    server.broadcast(first)
    server.broadcast(
        MessageNew(chat_jid=first.chat_jid, wa_id="1", ts=10, body="hola", status="read")
    )
    server.broadcast(msg("2", 20))
    got = [await next_event(it) for _ in range(3)]
    assert [(e.wa_id, e.status) for e in got if isinstance(e, MessageNew)] == [
        ("1", "sent"),
        ("1", "read"),
        ("2", "sent"),
    ]


async def test_run_survives_unexpected_exception(
    make_server: MakeServer, make_client: MakeClient
) -> None:
    await make_server()
    client = make_client()
    original = client._session
    calls = 0

    async def flaky(*args: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("inesperado")
        await original(*args)

    client._session = flaky  # type: ignore[method-assign]
    await client.start()
    await client.wait_connected(TIMEOUT)
    assert calls >= 2
    assert client.connected


async def test_non_socket_path_is_rejected(
    sock_path: Path, make_client: MakeClient, delays: list[float]
) -> None:
    _write(sock_path)
    client = make_client()
    await client.start()
    await until(lambda: client.last_error == "PermissionError")
    assert not client.connected
    assert delays


def test_socket_of_other_uid_is_rejected(
    sock_path: Path, make_client: MakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client()
    fake = os.stat_result((stat.S_IFSOCK | 0o600, 1, 1, 1, os.getuid() + 1, 0, 0, 0, 0, 0))
    monkeypatch.setattr(os, "lstat", lambda _p: fake)
    with pytest.raises(PermissionError):
        client._check_socket()


async def test_untrusted_peer_uid_is_rejected(
    make_server: MakeServer, make_client: MakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_server()
    monkeypatch.setattr("wsp.protocol.client.peer_uid", lambda _s: os.getuid() + 1)
    client = make_client()
    await client.start()
    await until(lambda: client.last_error == "PermissionError")
    assert not client.connected


async def test_backfill_events_have_start_and_end(
    make_server: MakeServer, make_client: MakeClient
) -> None:
    await make_server()
    client = make_client(since=0)
    await client.start()
    it = client.events()
    assert isinstance(await next_event(it), BackfillStart)
    assert isinstance(await next_event(it), BackfillEnd)
