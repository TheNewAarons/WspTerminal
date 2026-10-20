import asyncio
import os
import socket
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from tests.protocol.conftest import (
    CHAT,
    TIMEOUT,
    FakeBackfill,
    HandlerBox,
    Raw,
    msg,
    until,
)
from wsp.protocol.models import (
    MAX_LINE,
    AccountState,
    HandledCommand,
    MessageNew,
)
from wsp.protocol.server import AlreadyRunningError, CommandError, Server, sqlite_backfill
from wsp.store import db, repo
from wsp.store.repo import MessageIn

MakeServer = Callable[..., Awaitable[Server]]
Connect = Callable[[], Awaitable[Raw]]

LIST5 = {"limit": 5}


def _write(path: Path, text: str) -> None:
    path.write_text(text)


def _exists(path: Path) -> bool:
    return os.path.exists(path)


def _read(path: Path) -> str:
    return path.read_text()


async def _ready(connect: Connect, since: int | None = None) -> Raw:
    raw = await connect()
    env = await raw.hello(since)
    assert env.type == "ok"
    return raw


async def _code(raw: Raw) -> str:
    env = await raw.recv()
    assert env is not None
    return str(env.data["code"])


# ---- ciclo de vida del socket --------------------------------------------------------------


async def test_socket_mode_600(make_server: MakeServer, sock_path: Path) -> None:
    await make_server()
    assert stat.S_IMODE(os.stat(sock_path).st_mode) == 0o600
    assert stat.S_ISSOCK(os.stat(sock_path).st_mode)


async def test_stale_socket_is_removed(
    make_server: MakeServer, sock_path: Path, connect: Connect
) -> None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sock_path))
    s.close()  # el archivo queda huérfano
    assert _exists(sock_path)
    await make_server()
    await _ready(connect)


async def test_non_socket_file_is_not_touched(make_server: MakeServer, sock_path: Path) -> None:
    _write(sock_path, "datos")
    with pytest.raises(OSError, match="no es un socket") as ei:
        await make_server()
    assert not isinstance(ei.value, AlreadyRunningError)
    assert _read(sock_path) == "datos"


async def test_live_daemon_raises_already_running(
    make_server: MakeServer, sock_path: Path, connect: Connect, handler: HandlerBox
) -> None:
    await make_server()
    with pytest.raises(AlreadyRunningError):
        await make_server()
    await _ready(connect)  # el primero sigue sirviendo y conserva su socket
    assert _exists(sock_path)


async def test_path_too_long(sock_path: Path, handler: HandlerBox, backfill: FakeBackfill) -> None:
    server = Server(sock_path.parent / ("a" * 120), handler, backfill)
    with pytest.raises(OSError, match="demasiado larga"):
        await server.start()


# ---- framing y errores ---------------------------------------------------------------------


async def test_line_too_big_closes(make_server: MakeServer, connect: Connect) -> None:
    await make_server()
    raw = await connect()
    await raw.send_bytes(b"x" * (MAX_LINE + 2))  # sin salto: excede el límite del lector
    assert await _code(raw) == "line_too_big"
    assert await raw.recv() is None


async def test_three_invalid_lines_close(make_server: MakeServer, connect: Connect) -> None:
    await make_server()
    raw = await connect()
    await raw.send_bytes(b"basura\n" * 3)
    assert [await _code(raw) for _ in range(3)] == ["bad_json", "bad_json", "too_many_errors"]
    assert await raw.recv() is None


async def test_valid_command_resets_strikes(make_server: MakeServer, connect: Connect) -> None:
    await make_server()
    raw = await connect()
    await raw.send_bytes(b"basura\nbasura\n")
    await raw.send("ping", "r1")  # hello_required: línea válida, resetea
    await raw.send_bytes(b"basura\nbasura\n")
    await raw.send("ping", "r2")
    envs = await raw.recv_n(6)
    assert [e.type for e in envs] == ["error", "error", "fail", "error", "error", "fail"]
    assert envs[5].request_id == "r2"


async def test_command_before_hello(make_server: MakeServer, connect: Connect) -> None:
    await make_server()
    raw = await connect()
    await raw.send("list_chats", "r1", LIST5)
    env = await raw.recv()
    assert env is not None
    assert (env.type, env.request_id, env.data["code"]) == ("fail", "r1", "hello_required")


async def test_double_hello(make_server: MakeServer, connect: Connect) -> None:
    await make_server()
    raw = await _ready(connect)
    await raw.send("hello", "h2", {"client": "cli"})
    env = await raw.recv()
    assert env is not None
    assert (env.type, env.request_id, env.data["code"]) == ("fail", "h2", "already_hello")


async def test_ping_ok(make_server: MakeServer, connect: Connect) -> None:
    await make_server()
    raw = await _ready(connect)
    await raw.send("ping", "p1")
    env = await raw.recv()
    assert env is not None
    assert (env.type, env.request_id) == ("ok", "p1")


async def test_missing_request_id_gets_error_event(
    make_server: MakeServer, connect: Connect
) -> None:
    await make_server()
    raw = await _ready(connect)
    await raw.send("ping")
    assert await _code(raw) == "missing_request_id"


# ---- handler -------------------------------------------------------------------------------


async def test_handler_ok_data(
    make_server: MakeServer, connect: Connect, handler: HandlerBox
) -> None:
    await make_server()
    raw = await _ready(connect)
    await raw.send("list_chats", "r1", LIST5)
    env = await raw.recv()
    assert env is not None
    assert (env.type, env.data) == ("ok", {"type": "list_chats", "limit": 5})
    assert handler.seen == ["r1"]


async def test_handler_command_error(
    make_server: MakeServer, connect: Connect, handler: HandlerBox
) -> None:
    async def boom(rid: str, cmd: HandledCommand) -> dict[str, object]:
        raise CommandError("rate_limited", "espera")

    handler.fn = boom
    await make_server()
    raw = await _ready(connect)
    await raw.send("list_chats", "r1", LIST5)
    env = await raw.recv()
    assert env is not None
    assert env.type == "fail"
    assert env.data == {"code": "rate_limited", "message": "espera"}


async def test_handler_unexpected_exception_is_internal(
    make_server: MakeServer, connect: Connect, handler: HandlerBox
) -> None:
    async def boom(rid: str, cmd: HandledCommand) -> dict[str, object]:
        raise RuntimeError("SECRETO-detalle")

    handler.fn = boom
    await make_server()
    raw = await _ready(connect)
    await raw.send("list_chats", "r1", LIST5)
    env = await raw.recv()
    assert env is not None
    assert env.type == "fail"
    assert env.data["code"] == "internal"
    assert "SECRETO" not in str(env.data)
    await raw.send("ping", "r2")  # la conexión sigue viva
    env2 = await raw.recv()
    assert env2 is not None
    assert env2.type == "ok"


# ---- broadcast -----------------------------------------------------------------------------


async def test_broadcast_to_three_clients_in_order(
    make_server: MakeServer, connect: Connect
) -> None:
    server = await make_server()
    raws = [await _ready(connect) for _ in range(3)]
    await until(lambda: server.client_count == 3)
    for i in range(10):
        server.broadcast(msg(str(i), i))
    for raw in raws:
        envs = await raw.recv_n(10)
        assert [e.data["wa_id"] for e in envs] == [str(i) for i in range(10)]
        assert {e.type for e in envs} == {"message.new"}


async def test_account_state_replayed_on_hello(make_server: MakeServer, connect: Connect) -> None:
    server = await make_server()
    server.broadcast(AccountState(state="connected"))  # sin clientes aún
    raw = await _ready(connect)
    env = await raw.recv()
    assert env is not None
    assert (env.type, env.data["state"]) == ("account.state", "connected")


async def test_unsubscribed_client_gets_no_broadcast(
    make_server: MakeServer, connect: Connect
) -> None:
    server = await make_server()
    idle = await connect()  # sin hello
    ready = await _ready(connect)
    server.broadcast(msg("1", 1))
    env = await ready.recv()
    assert env is not None
    assert env.data["wa_id"] == "1"
    with pytest.raises(TimeoutError):
        await idle.recv(0.2)


# ---- consumidor lento ----------------------------------------------------------------------


async def test_slow_consumer_cut_others_keep_receiving(
    make_server: MakeServer, connect: Connect, sock_path: Path
) -> None:
    server = await make_server(queue_size=16, write_timeout=0.3)
    reader, writer = await asyncio.open_unix_connection(str(sock_path), limit=MAX_LINE + 1)
    slow = Raw(reader, writer)
    await slow.send("hello", "h1", {"client": "cli"})  # hace hello y NO lee
    good = await _ready(connect)
    await until(lambda: server.client_count == 2)

    total = 100
    big = "x" * 30_000

    async def read_good() -> list[str]:
        out: list[str] = []
        while len(out) < total:
            env = await good.recv()
            assert env is not None
            out.append(str(env.data["wa_id"]))
        return out

    good_task = asyncio.create_task(read_good())
    for i in range(total):
        server.broadcast(msg(str(i), i, body=big))
        await asyncio.sleep(0.002)
    assert await asyncio.wait_for(good_task, 5) == [str(i) for i in range(total)]

    received = 0
    error_code: str | None = None
    try:
        while True:
            env = await slow.recv(2)
            if env is None:
                break
            if env.type == "error":
                error_code = str(env.data["code"])
            elif env.type == "message.new":
                received += 1
    except ConnectionError:
        pass  # cortado con RST
    assert received < total
    assert error_code in (None, "slow_consumer")
    await until(lambda: server.client_count == 1)
    await slow.close()


# ---- relleno sin huecos --------------------------------------------------------------------


async def test_backfill_no_gaps_no_duplicates(
    make_server: MakeServer, connect: Connect, backfill: FakeBackfill
) -> None:
    backfill.events = [msg("1", 10), msg("2", 11)]
    backfill.gate = asyncio.Event()
    server = await make_server()
    raw = await connect()
    await raw.send("hello", "h1", {"client": "cli", "since": 10})
    ok = await raw.recv()
    assert ok is not None
    assert ok.type == "ok"
    await asyncio.wait_for(backfill.started.wait(), TIMEOUT)

    # Durante el backfill: duplicados (mismo chat_jid+wa_id) y nuevos.
    server.broadcast(msg("2", 11))
    server.broadcast(msg("3", 12))
    server.broadcast(msg("1", 10))
    server.broadcast(msg("4", 13))
    server.broadcast(msg("2", 11, chat_jid="otro@s.whatsapp.net"))  # otra clave: no duplica
    backfill.gate.set()

    other = "otro@s.whatsapp.net"
    envs = await raw.recv_n(7)
    seq = [(e.type, e.data.get("chat_jid"), e.data.get("wa_id")) for e in envs]
    assert seq == [
        ("backfill.start", None, None),
        ("message.new", CHAT, "1"),
        ("message.new", CHAT, "2"),
        ("backfill.end", None, None),
        ("message.new", CHAT, "3"),
        ("message.new", CHAT, "4"),
        ("message.new", other, "2"),
    ]
    assert envs[0].data == {"since": 10}
    assert envs[3].data == {"count": 2, "truncated": False}
    with pytest.raises(TimeoutError):
        await raw.recv(0.2)  # nada más: sin duplicados


async def test_live_events_after_backfill_flow(
    make_server: MakeServer, connect: Connect, backfill: FakeBackfill
) -> None:
    backfill.events = [msg("1", 10)]
    server = await make_server()
    raw = await _ready(connect, since=0)
    envs = await raw.recv_n(3)
    assert [e.type for e in envs] == ["backfill.start", "message.new", "backfill.end"]
    server.broadcast(msg("2", 11))
    env = await raw.recv()
    assert env is not None
    assert env.data["wa_id"] == "2"
    assert backfill.calls == [0]


# ---- sqlite_backfill -----------------------------------------------------------------------


def _seed(path: Path, stamps: list[int]) -> None:
    conn = db.open_write(path)
    try:
        repo.upsert_chat(conn, CHAT)
        for i, ts in enumerate(stamps):
            repo.upsert_message(conn, MessageIn(CHAT, f"w{i}", ts, body=f"m{i}"))
    finally:
        conn.close()


async def test_sqlite_backfill_since_is_inclusive(store_db: Path) -> None:
    _seed(store_db, [100, 200, 300])
    source = sqlite_backfill(store_db)
    events, truncated = await source(200)
    assert [(e.wa_id, e.ts, e.body) for e in events] == [("w1", 200, "m1"), ("w2", 300, "m2")]
    assert all(isinstance(e, MessageNew) for e in events)
    assert truncated is False
    all_events, _ = await source(0)
    assert [e.ts for e in all_events] == [100, 200, 300]
    none, _ = await source(301)
    assert none == []


async def test_sqlite_backfill_truncated(store_db: Path) -> None:
    n = repo.MAX_LIMIT
    _seed(store_db, list(range(1, n + 1)))
    events, truncated = await sqlite_backfill(store_db)(0)
    assert len(events) == n
    assert truncated is True
    events2, truncated2 = await sqlite_backfill(store_db)(2)  # n-1 mensajes: cabe entero
    assert len(events2) == n - 1
    assert truncated2 is False


async def test_server_with_sqlite_backfill(
    sock_path: Path, store_db: Path, connect: Connect, handler: HandlerBox
) -> None:
    _seed(store_db, [100, 200])
    server = Server(sock_path, handler, sqlite_backfill(store_db))
    await server.start()
    try:
        raw = await _ready(connect, since=200)
        envs = await raw.recv_n(3)
        assert [e.type for e in envs] == ["backfill.start", "message.new", "backfill.end"]
        assert envs[1].data["ts"] == 200
    finally:
        await server.stop()
