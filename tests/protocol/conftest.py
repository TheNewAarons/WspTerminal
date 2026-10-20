"""Fixtures del protocolo: socket Unix en ruta corta, servidor, cliente crudo y fakes."""

import asyncio
import contextlib
import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from wsp.protocol.client import Client
from wsp.protocol.models import (
    MAX_LINE,
    Envelope,
    Event,
    HandledCommand,
    MessageNew,
    decode_line,
    encode,
    read_line,
)
from wsp.protocol.server import Server
from wsp.store import db, migrate

CHAT = "5215500000000@s.whatsapp.net"
TIMEOUT = 3.0


def msg(wa_id: str, ts: int, chat_jid: str = CHAT, body: str | None = "hola") -> MessageNew:
    return MessageNew(chat_jid=chat_jid, wa_id=wa_id, ts=ts, body=body)


async def until(pred: Callable[[], bool], timeout: float = TIMEOUT) -> None:  # noqa: ASYNC109
    """Espera (con polling corto) a que `pred()` sea verdadero."""
    async with asyncio.timeout(timeout):
        while not pred():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def next_event(it: AsyncIterator[Event], timeout: float = TIMEOUT) -> Event:  # noqa: ASYNC109
    return await asyncio.wait_for(anext(it), timeout)


class FakeBackfill:
    """BackfillSource falso: devuelve `events` con ts >= since; puede bloquearse en `gate`."""

    def __init__(self) -> None:
        self.events: list[MessageNew] = []
        self.truncated = False
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()
        self.calls: list[int] = []

    async def __call__(self, since: int) -> tuple[list[MessageNew], bool]:
        self.calls.append(since)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return [e for e in self.events if e.ts >= since], self.truncated


class HandlerBox:
    """Handler intercambiable: `fn` se reemplaza en cada test; `seen` guarda los request_id."""

    def __init__(self) -> None:
        self.fn: Callable[[str, HandledCommand], Awaitable[Mapping[str, Any]]] = self._default
        self.seen: list[str] = []

    @staticmethod
    async def _default(rid: str, cmd: HandledCommand) -> Mapping[str, Any]:
        return {"type": cmd.TYPE, "limit": getattr(cmd, "limit", None)}

    async def __call__(self, rid: str, cmd: HandledCommand) -> Mapping[str, Any]:
        self.seen.append(rid)
        return await self.fn(rid, cmd)


class Raw:
    """Cliente crudo de línea: sin lógica de reconexión, para probar el servidor."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer

    async def send_bytes(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()

    async def send(
        self, type_: str, rid: str | None = None, data: Mapping[str, Any] | None = None
    ) -> None:
        env = Envelope(type=type_, request_id=rid, data=dict(data or {}))
        await self.send_bytes(encode(env))

    async def recv(self, timeout: float = TIMEOUT) -> Envelope | None:  # noqa: ASYNC109
        """Siguiente envelope; None en EOF."""
        line = await asyncio.wait_for(read_line(self.reader), timeout)
        return None if line is None else decode_line(line)

    async def recv_n(self, n: int) -> list[Envelope]:
        out: list[Envelope] = []
        for _ in range(n):
            env = await self.recv()
            assert env is not None, "EOF inesperado"
            out.append(env)
        return out

    async def hello(self, since: int | None = None, rid: str = "h1") -> Envelope:
        data: dict[str, Any] = {"client": "cli"}
        if since is not None:
            data["since"] = since
        await self.send("hello", rid, data)
        env = await self.recv()
        assert env is not None
        return env

    async def close(self) -> None:
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()


@pytest.fixture
def sock_path() -> Iterator[Path]:
    d = tempfile.mkdtemp(prefix="wsp-")
    path = Path(d) / "s.sock"
    assert len(os.fsencode(path)) < 100, "ruta de socket demasiado larga para AF_UNIX"
    try:
        yield path
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def backfill() -> FakeBackfill:
    return FakeBackfill()


@pytest.fixture
def handler() -> HandlerBox:
    return HandlerBox()


@pytest.fixture
async def make_server(
    sock_path: Path, backfill: FakeBackfill, handler: HandlerBox
) -> AsyncIterator[Callable[..., Awaitable[Server]]]:
    servers: list[Server] = []

    async def factory(**kwargs: Any) -> Server:
        server = Server(sock_path, handler, backfill, **kwargs)
        await server.start()
        servers.append(server)
        return server

    yield factory
    for server in reversed(servers):
        await server.stop()


@pytest.fixture
async def connect(sock_path: Path) -> AsyncIterator[Callable[[], Awaitable[Raw]]]:
    raws: list[Raw] = []

    async def factory() -> Raw:
        reader, writer = await asyncio.open_unix_connection(str(sock_path), limit=MAX_LINE + 1)
        raw = Raw(reader, writer)
        raws.append(raw)
        return raw

    yield factory
    for raw in raws:
        await raw.close()


@pytest.fixture
def delays() -> list[float]:
    """Esperas pedidas por el cliente a su `sleep` inyectado."""
    return []


@pytest.fixture
async def make_client(sock_path: Path, delays: list[float]) -> AsyncIterator[Callable[..., Client]]:
    clients: list[Client] = []

    async def fast_sleep(delay: float) -> None:
        delays.append(delay)
        await asyncio.sleep(0.01)

    def factory(**kwargs: Any) -> Client:
        kwargs.setdefault("rand", lambda: 0.0)
        kwargs.setdefault("sleep", fast_sleep)
        client = Client(sock_path, **kwargs)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.stop()


@pytest.fixture
def store_db(tmp_path: Path) -> Path:
    """DB temporal con esquema migrado (nunca la real)."""
    path = tmp_path / "wsp.db"
    conn = db.open_write(path)
    try:
        migrate.migrate(conn)
    finally:
        conn.close()
    return path
