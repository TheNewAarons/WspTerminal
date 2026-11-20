"""Cliente del socket Unix (lado CLI): reconexión con backoff, resume por `since` y requests.

Tras reconectar envía `hello{since=<último ts visto>}`; el daemon rellena desde SQLite y el
cliente descarta duplicados por (chat_jid, wa_id). Los comandos no se reintentan solos: para
reintentar de forma idempotente, reusar el mismo `request_id`.
"""

import asyncio
import contextlib
import logging
import os
import random
import stat
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Literal, Self

from wsp.protocol.models import (
    MAX_LINE,
    BackfillEnd,
    Command,
    Envelope,
    Event,
    Hello,
    MessageNew,
    ProtocolError,
    decode_line,
    encode,
    parse_event,
    read_line,
    to_envelope,
)
from wsp.protocol.peer import peer_uid

log = logging.getLogger(__name__)

BACKOFF_BASE: Final = 0.5
BACKOFF_CAP: Final = 10.0
_CLOSED: Final = object()


class ConnectionLost(ConnectionError):
    """La conexión con el daemon se cayó (o aún no existe)."""


class RequestTimeout(TimeoutError):
    """El daemon no respondió a tiempo (el comando pudo haberse ejecutado)."""


class _Resync(Exception):
    """Backfill truncado: reconectar de inmediato con el último ts del backfill."""


class RemoteError(Exception):
    """El daemon respondió `fail{code, message}`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def backoff_delay(
    attempt: int, rand: float, *, base: float = BACKOFF_BASE, cap: float = BACKOFF_CAP
) -> float:
    """Exponencial 0.5 s → 10 s con jitter entre 50 % y 100 % (`rand` ∈ [0, 1))."""
    return min(cap, base * float(2 ** min(attempt, 16))) * (0.5 + 0.5 * rand)


class Client:
    def __init__(
        self,
        path: Path,
        *,
        kind: Literal["cli", "tail"] = "cli",
        since: int | None = None,
        queue_size: int = 1024,
        seen_size: int = 4096,
        rand: Callable[[], float] = random.random,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._path = path
        self._kind = kind
        self._last_ts = since
        self._events: asyncio.Queue[Any] = asyncio.Queue(queue_size)
        self._seen: OrderedDict[tuple[str, str], MessageNew] = OrderedDict()
        self._seen_size = seen_size
        self._rand = rand
        self._sleep = sleep
        self._ready = asyncio.Event()
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[Mapping[str, Any]]] = {}
        self._task: asyncio.Task[None] | None = None
        self._attempt = 0
        self._session_since: int | None = None
        self.last_error: str | None = None

    @property
    def last_ts(self) -> int | None:
        """Mayor ts de `message.new` visto; se envía como `since` al reconectar."""
        return self._last_ts

    @property
    def connected(self) -> bool:
        return self._ready.is_set()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="wsp-client")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._drop()
        while not self._events.empty():
            self._events.get_nowait()
        self._events.put_nowait(_CLOSED)

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()

    async def wait_connected(self, timeout: float | None = None) -> None:  # noqa: ASYNC109
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except TimeoutError:
            raise ConnectionLost("sin conexión con el daemon") from None

    async def events(self) -> AsyncIterator[Event]:
        """Eventos del daemon (incluye backfill.start/end); termina tras `stop()`."""
        while True:
            item = await self._events.get()
            if item is _CLOSED:
                return
            yield item

    def __aiter__(self) -> AsyncIterator[Event]:
        return self.events()

    # ---- requests -----------------------------------------------------------------------

    async def request(
        self,
        cmd: Command,
        *,
        request_id: str | None = None,
        timeout: float = 10.0,  # noqa: ASYNC109
    ) -> Mapping[str, Any]:
        """Envía un comando y espera `ok` (data) o `fail` (RemoteError) correlacionado."""
        rid = request_id or uuid.uuid4().hex
        if rid in self._pending:
            raise ValueError("request_id en uso")
        try:
            async with asyncio.timeout(timeout):
                await self._ready.wait()
                fut: asyncio.Future[Mapping[str, Any]] = asyncio.get_running_loop().create_future()
                self._pending[rid] = fut
                try:
                    await self._write(encode(to_envelope(cmd, rid)))
                    return await fut
                finally:
                    self._pending.pop(rid, None)
        except TimeoutError:
            raise RequestTimeout(f"sin respuesta en {timeout:g}s") from None

    async def _write(self, line: bytes) -> None:
        writer = self._writer
        if writer is None:
            raise ConnectionLost("sin conexión con el daemon")
        try:
            async with self._write_lock:
                writer.write(line)
                await writer.drain()
        except ConnectionError as e:
            raise ConnectionLost(str(e)) from None

    # ---- conexión -----------------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            resync = False
            try:
                self._check_socket()
                reader, writer = await asyncio.open_unix_connection(
                    str(self._path), limit=MAX_LINE + 1
                )
            except OSError as e:
                self.last_error = type(e).__name__
            else:
                try:
                    await self._session(reader, writer)
                except _Resync:
                    resync = self._last_ts != self._session_since  # solo si avanzó
                except Exception as e:  # el bucle nunca debe morir en silencio
                    self.last_error = type(e).__name__
                finally:
                    self._drop()
                    writer.close()
            if resync:
                continue
            await self._sleep(backoff_delay(self._attempt, self._rand()))
            self._attempt += 1

    def _check_socket(self) -> None:
        """Solo habla con un socket propio (evita un socket suplantado)."""
        st = os.lstat(self._path)
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
            raise PermissionError("socket no confiable")

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        if sock is None or peer_uid(sock) != os.getuid():
            raise PermissionError("peer no confiable")
        rid = "hello-" + uuid.uuid4().hex[:12]
        self._session_since = self._last_ts
        hello = Hello(since=self._last_ts, client=self._kind)
        writer.write(encode(to_envelope(hello, rid)))
        await writer.drain()
        env = await self._read(reader)
        if env.type != "ok" or env.request_id != rid:
            raise ConnectionLost("hello rechazado")
        self._writer = writer
        self._attempt = 0
        self.last_error = None
        self._ready.set()
        while True:
            self._dispatch(await self._read(reader))

    async def _read(self, reader: asyncio.StreamReader) -> Envelope:
        line = await read_line(reader)
        if line is None:
            raise ConnectionLost("el daemon cerró la conexión")
        return decode_line(line)

    def _dispatch(self, env: Envelope) -> None:
        if env.type in ("ok", "fail"):  # nunca pasan por la cola de eventos
            fut = self._pending.get(env.request_id or "")
            if fut is not None and not fut.done():
                if env.type == "ok":
                    fut.set_result(env.data)
                else:
                    fut.set_exception(
                        RemoteError(str(env.data.get("code", "")), str(env.data.get("message", "")))
                    )
            return
        try:
            event = parse_event(env)
        except ProtocolError as e:
            log.warning("evento ignorado code=%s", e.code)  # tipo nuevo o inválido
            return
        key = (event.chat_jid, event.wa_id) if isinstance(event, MessageNew) else None
        if key is not None and self._seen.get(key) == event:
            return  # reenvío idéntico (backfill inclusivo); un cambio de estado sí pasa
        try:
            self._events.put_nowait(event)
        except asyncio.QueueFull:  # consumidor lento: reconectar y recuperar desde `since`
            raise ConnectionLost("consumidor lento") from None
        if isinstance(event, MessageNew) and key is not None:
            self._seen[key] = event
            self._seen.move_to_end(key)
            if len(self._seen) > self._seen_size:
                self._seen.popitem(last=False)
            if self._last_ts is None or event.ts > self._last_ts:
                self._last_ts = event.ts
        elif isinstance(event, BackfillEnd) and event.truncated:
            raise _Resync

    def _drop(self) -> None:
        self._ready.clear()
        self._writer = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionLost("conexión perdida"))
