import asyncio
from collections.abc import Awaitable, Callable

import pytest

from tests.protocol.conftest import TIMEOUT, FakeBackfill, HandlerBox, msg, next_event, until
from wsp.protocol.client import (
    BACKOFF_CAP,
    Client,
    ConnectionLost,
    RemoteError,
    RequestTimeout,
    backoff_delay,
)
from wsp.protocol.models import (
    BackfillEnd,
    BackfillStart,
    Event,
    HandledCommand,
    ListChats,
    MessageNew,
    Ping,
)
from wsp.protocol.server import CommandError, Server

MakeServer = Callable[..., Awaitable[Server]]
MakeClient = Callable[..., Client]


async def _connected(make_client: MakeClient, **kwargs: object) -> Client:
    client = make_client(**kwargs)
    await client.start()
    await client.wait_connected(TIMEOUT)
    return client


# ---- backoff -------------------------------------------------------------------------------


def test_backoff_grows_and_caps() -> None:
    lows = [backoff_delay(n, 0.0) for n in range(8)]
    highs = [backoff_delay(n, 1.0) for n in range(8)]
    assert lows[0] == pytest.approx(0.25)
    assert highs[:6] == pytest.approx([0.5, 1.0, 2.0, 4.0, 8.0, 10.0])
    assert highs[5:] == pytest.approx([BACKOFF_CAP] * 3)
    assert lows[7] == pytest.approx(BACKOFF_CAP / 2)


def test_backoff_jitter_between_50_and_100_percent() -> None:
    for attempt in range(10):
        full = min(BACKOFF_CAP, 0.5 * 2**attempt)
        for r in (0.0, 0.25, 0.5, 0.999):
            assert 0.5 * full <= backoff_delay(attempt, r) <= full


def test_backoff_huge_attempt_does_not_overflow() -> None:
    assert backoff_delay(10_000, 0.5) == pytest.approx(BACKOFF_CAP * 0.75)


# ---- eventos y requests --------------------------------------------------------------------


async def test_receives_events(make_server: MakeServer, make_client: MakeClient) -> None:
    server = await make_server()
    client = await _connected(make_client)
    it = client.events()
    server.broadcast(msg("1", 10))
    ev = await next_event(it)
    assert isinstance(ev, MessageNew)
    assert ev.wa_id == "1"
    assert client.last_ts == 10
    assert client.connected


async def test_request_correlates_by_request_id(
    make_server: MakeServer, make_client: MakeClient, handler: HandlerBox
) -> None:
    await make_server()
    client = await _connected(make_client)
    results = await asyncio.gather(*[client.request(ListChats(limit=i)) for i in range(1, 6)])
    assert [r["limit"] for r in results] == [1, 2, 3, 4, 5]
    assert len(set(handler.seen)) == 5
    assert await client.request(ListChats(limit=9), request_id="mi-id") == {
        "type": "list_chats",
        "limit": 9,
    }
    assert "mi-id" in handler.seen
    assert await client.request(Ping()) == {}


async def test_request_remote_error(
    make_server: MakeServer, make_client: MakeClient, handler: HandlerBox
) -> None:
    async def nope(rid: str, cmd: HandledCommand) -> dict[str, object]:
        raise CommandError("rate_limited", "espera")

    handler.fn = nope
    await make_server()
    client = await _connected(make_client)
    with pytest.raises(RemoteError) as ei:
        await client.request(ListChats(limit=1))
    assert (ei.value.code, ei.value.message) == ("rate_limited", "espera")


async def test_request_timeout(
    make_server: MakeServer, make_client: MakeClient, handler: HandlerBox
) -> None:
    release = asyncio.Event()

    async def slow(rid: str, cmd: HandledCommand) -> dict[str, object]:
        await release.wait()
        return {}

    handler.fn = slow
    await make_server()
    client = await _connected(make_client)
    try:
        with pytest.raises(RequestTimeout):
            await client.request(ListChats(limit=1), timeout=0.2)
    finally:
        release.set()


async def test_duplicate_request_id_in_flight(
    make_server: MakeServer, make_client: MakeClient, handler: HandlerBox
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(rid: str, cmd: HandledCommand) -> dict[str, object]:
        entered.set()
        await release.wait()
        return {"done": True}

    handler.fn = slow
    await make_server()
    client = await _connected(make_client)
    first = asyncio.create_task(client.request(ListChats(limit=1), request_id="dup"))
    await asyncio.wait_for(entered.wait(), TIMEOUT)
    with pytest.raises(ValueError, match="en uso"):
        await client.request(ListChats(limit=1), request_id="dup")
    release.set()
    assert await asyncio.wait_for(first, TIMEOUT) == {"done": True}


async def test_pending_requests_get_connection_lost(
    make_server: MakeServer, make_client: MakeClient, handler: HandlerBox
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(rid: str, cmd: HandledCommand) -> dict[str, object]:
        entered.set()
        await release.wait()
        return {}

    handler.fn = slow
    server = await make_server()
    client = await _connected(make_client)
    task = asyncio.create_task(client.request(ListChats(limit=1), timeout=5))
    await asyncio.wait_for(entered.wait(), TIMEOUT)
    await server.stop()
    try:
        with pytest.raises(ConnectionLost):
            await asyncio.wait_for(task, TIMEOUT)
    finally:
        release.set()
    assert not client.connected


# ---- reconexión ----------------------------------------------------------------------------


async def test_reconnect_resumes_from_last_ts_and_dedupes(
    make_server: MakeServer,
    make_client: MakeClient,
    backfill: FakeBackfill,
    delays: list[float],
) -> None:
    server = await make_server()
    client = await _connected(make_client)
    it = client.events()
    server.broadcast(msg("1", 10))
    server.broadcast(msg("2", 20))
    assert [(await next_event(it)).TYPE for _ in range(2)] == ["message.new"] * 2
    assert client.last_ts == 20

    await server.stop()
    await until(lambda: not client.connected)
    backfill.events = [msg("2", 20), msg("3", 30)]  # 2 ya visto: se descarta
    backfill.calls.clear()
    server2 = await make_server()
    await client.wait_connected(TIMEOUT)

    seen: list[Event] = [await next_event(it) for _ in range(3)]
    assert backfill.calls == [20]  # since = último ts visto (inclusivo)
    assert isinstance(seen[0], BackfillStart)
    assert seen[0].since == 20
    assert isinstance(seen[1], MessageNew)
    assert seen[1].wa_id == "3"
    assert isinstance(seen[2], BackfillEnd)
    assert seen[2].count == 2
    assert client.last_ts == 30

    server2.broadcast(msg("3", 30))  # duplicado en vivo
    server2.broadcast(msg("4", 40))
    ev = await next_event(it)
    assert isinstance(ev, MessageNew)
    assert ev.wa_id == "4"
    assert client.last_ts == 40

    assert delays
    assert all(d == backoff_delay(i, 0.0) for i, d in enumerate(delays))


async def test_initial_since_sent_in_hello(
    make_server: MakeServer, make_client: MakeClient, backfill: FakeBackfill
) -> None:
    await make_server()
    client = await _connected(make_client, since=50)
    it = client.events()
    assert isinstance(await next_event(it), BackfillStart)
    assert isinstance(await next_event(it), BackfillEnd)
    assert backfill.calls == [50]


async def test_retries_until_server_appears(
    make_server: MakeServer, make_client: MakeClient, delays: list[float]
) -> None:
    client = make_client()
    await client.start()
    await until(lambda: len(delays) >= 3)
    assert client.last_error is not None
    assert not client.connected
    await make_server()
    await client.wait_connected(TIMEOUT)
    assert client.last_error is None


# ---- stop ----------------------------------------------------------------------------------


async def test_stop_ends_event_iterator(make_server: MakeServer, make_client: MakeClient) -> None:
    await make_server()
    client = await _connected(make_client)

    async def drain() -> list[Event]:
        return [e async for e in client.events()]

    task = asyncio.create_task(drain())
    await asyncio.sleep(0.05)
    await client.stop()
    assert await asyncio.wait_for(task, TIMEOUT) == []
    assert not client.connected


async def test_stop_without_server(make_client: MakeClient) -> None:
    client = make_client()
    await client.start()
    with pytest.raises(ConnectionLost):
        await client.wait_connected(0.05)

    async def drain() -> list[Event]:
        return [e async for e in client]

    task = asyncio.create_task(drain())
    await asyncio.sleep(0.02)
    await client.stop()
    assert await asyncio.wait_for(task, TIMEOUT) == []
