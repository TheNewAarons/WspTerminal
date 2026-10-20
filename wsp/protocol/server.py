"""Servidor del socket Unix (lado daemon): broadcast, backfill sin huecos y cola acotada.

Orden de un `hello{since}`: ok → [account.state] → backfill.start → mensajes → backfill.end →
eventos en vivo. El cliente se registra en el broadcast ANTES de leer SQLite y los eventos que
llegan durante el backfill se retienen (deduplicados por chat_jid+wa_id), así no hay huecos.
"""

import asyncio
import contextlib
import logging
import os
import socket
import stat
import struct
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from wsp.protocol.models import (
    MAX_LINE,
    AccountState,
    BackfillEnd,
    BackfillStart,
    Envelope,
    Event,
    HandledCommand,
    Hello,
    MessageNew,
    Payload,
    Ping,
    ProtocolError,
    decode_line,
    encode,
    error_envelope,
    fail,
    ok,
    parse_command,
    read_line,
    to_envelope,
)
from wsp.store import db, repo

log = logging.getLogger(__name__)

_SOCKET_MAX: Final = 104  # límite de AF_UNIX en macOS
_MAX_STRIKES: Final = 3  # líneas inválidas consecutivas antes de cortar
_KICK_GRACE: Final = 1.0
_SOL_LOCAL: Final = 0
_LOCAL_PEERCRED: Final = 0x001  # <sys/un.h>

Handler = Callable[[str, HandledCommand], Awaitable[Mapping[str, Any]]]
"""(request_id, comando) → data del `ok`. Lanza `CommandError` para un `fail` controlado."""
BackfillSource = Callable[[int], Awaitable[tuple[list[MessageNew], bool]]]
"""since → (mensajes con ts >= since en orden, truncado)."""


class CommandError(Exception):
    """Falla controlada de un comando; se responde `fail{code, message}`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class AlreadyRunningError(OSError):
    """Otro daemon está escuchando en el socket."""


def peer_uid(sock: socket.socket) -> int | None:
    """UID del proceso en el otro extremo (equivalente a getpeereid); None si no se puede."""
    try:
        so_peercred: int | None = getattr(socket, "SO_PEERCRED", None)
        if so_peercred is not None:  # Linux: struct ucred {pid, uid, gid}
            data = sock.getsockopt(socket.SOL_SOCKET, so_peercred, struct.calcsize("3i"))
            return int(struct.unpack_from("3i", data)[1])
        data = sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERCRED, 256)  # macOS: struct xucred
        return int(struct.unpack_from("=II", data)[1])  # (cr_version, cr_uid)
    except (OSError, struct.error):
        return None


def sqlite_backfill(db_path: Path | None = None) -> BackfillSource:
    """Fuente de backfill desde SQLite (conexión de solo lectura, fuera del loop)."""

    def read(since: int) -> tuple[list[MessageNew], bool]:
        conn = db.open_read(db_path)
        try:
            rows = repo.messages_since(conn, max(since - 1, 0), repo.MAX_LIMIT)
        finally:
            conn.close()
        events = [
            MessageNew(
                chat_jid=r.chat_jid,
                wa_id=r.wa_id,
                sender_jid=r.sender_jid,
                from_me=r.from_me,
                ts=r.ts,
                kind=r.kind,
                body=r.body,
                quoted_wa_id=r.quoted_wa_id,
                status=r.status,
                error=r.error,
                edited_at=r.edited_at,
                deleted_at=r.deleted_at,
            )
            for r in rows
        ]
        return events, len(rows) >= repo.MAX_LIMIT

    async def source(since: int) -> tuple[list[MessageNew], bool]:
        return await asyncio.to_thread(read, since)

    return source


def _key(event: Payload) -> tuple[str, str] | None:
    return (event.chat_jid, event.wa_id) if isinstance(event, MessageNew) else None


@dataclass(eq=False)
class _Conn:
    id: int
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    queue: asyncio.Queue[bytes]
    client: str | None = None  # None hasta recibir hello
    subscribed: bool = False
    # Retención de eventos en vivo mientras corre el backfill (None = en vivo).
    pending: deque[tuple[Payload, bytes]] | None = None
    sent_keys: set[tuple[str, str]] = field(default_factory=set)
    writer_task: asyncio.Task[None] | None = None
    closing: bool = False


class Server:
    def __init__(
        self,
        path: Path,
        handler: Handler,
        backfill: BackfillSource,
        *,
        queue_size: int = 256,
        write_timeout: float = 10.0,
    ) -> None:
        self._path = path
        self._handler = handler
        self._backfill = backfill
        self._queue_size = queue_size
        self._write_timeout = write_timeout
        self._server: asyncio.Server | None = None
        self._conns: set[_Conn] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._account: AccountState | None = None
        self._next_id = 0

    @property
    def client_count(self) -> int:
        return sum(1 for c in self._conns if c.subscribed)

    # ---- ciclo de vida ------------------------------------------------------------------

    async def start(self) -> None:
        if len(os.fsencode(self._path)) >= _SOCKET_MAX:
            raise OSError(f"ruta de socket demasiado larga (>= {_SOCKET_MAX} bytes)")
        await _remove_stale(self._path)
        old = os.umask(0o077)
        try:
            self._server = await asyncio.start_unix_server(
                self._on_connect, path=str(self._path), limit=MAX_LINE + 1
            )
        finally:
            os.umask(old)
        os.chmod(self._path, 0o600)

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
        for c in list(self._conns):
            self._abort(c)
        for t in list(self._tasks):
            t.cancel()
        if server is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(server.wait_closed(), 2.0)
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()

    # ---- broadcast ----------------------------------------------------------------------

    def broadcast(self, payload: Event) -> None:
        """Encola el evento a todos los clientes suscritos sin bloquear."""
        if isinstance(payload, AccountState):
            self._account = payload
        try:
            line = encode(to_envelope(payload))
        except ProtocolError:
            log.warning("broadcast descartado code=line_too_big type=%s", payload.TYPE)
            return
        for c in list(self._conns):
            if not c.subscribed or c.closing:
                continue
            if c.pending is not None:
                if len(c.pending) >= self._queue_size:
                    self._kick(c, "slow_consumer")
                else:
                    c.pending.append((payload, line))
            else:
                self._offer(c, line)

    def _offer(self, c: _Conn, line: bytes) -> None:
        try:
            c.queue.put_nowait(line)
        except asyncio.QueueFull:
            self._kick(c, "slow_consumer")

    # ---- conexión -----------------------------------------------------------------------

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        if sock is None or peer_uid(sock) != os.getuid():
            log.warning("conexión rechazada code=bad_peer")
            writer.transport.abort()
            return
        self._next_id += 1
        c = _Conn(self._next_id, reader, writer, asyncio.Queue(self._queue_size))
        self._conns.add(c)
        try:
            await self._read_loop(c)
        except (ConnectionError, asyncio.IncompleteReadError, TimeoutError):
            pass
        finally:
            self._conns.discard(c)
            c.closing = True
            if c.writer_task is not None:
                c.writer_task.cancel()
            c.writer.transport.abort()

    async def _read_loop(self, c: _Conn) -> None:
        strikes = 0
        while not c.closing:
            try:
                line = await read_line(c.reader)
            except ProtocolError as e:
                await self._send_direct(c, error_envelope(e.code, e.message))
                return
            if line is None:
                return
            try:
                env = decode_line(line)
            except ProtocolError as e:
                strikes += 1
                if strikes >= _MAX_STRIKES:
                    await self._send_direct(c, error_envelope("too_many_errors", "cerrando"))
                    return
                await self._reply(c, error_envelope(e.code, e.message))
                continue
            if await self._dispatch(c, env):
                strikes = 0
            else:
                strikes += 1
                if strikes >= _MAX_STRIKES:
                    await self._send_direct(c, error_envelope("too_many_errors", "cerrando"))
                    return

    async def _dispatch(self, c: _Conn, env: Envelope) -> bool:
        """Procesa un comando; False si la línea fue inválida (cuenta como strike)."""
        try:
            rid, cmd = parse_command(env)
        except ProtocolError as e:
            await self._reply(
                c,
                fail(env.request_id, e.code, e.message)
                if env.request_id
                else error_envelope(e.code, e.message),
            )
            return False
        if isinstance(cmd, Hello):
            if c.client is not None:
                await self._reply(c, fail(rid, "already_hello", "hello ya enviado"))
                return True
            await self._hello(c, rid, cmd)
            return True
        if c.client is None:
            await self._reply(c, fail(rid, "hello_required", "envía hello primero"))
            return True
        if isinstance(cmd, Ping):
            await self._reply(c, ok(rid))
            return True
        try:
            data = await self._handler(rid, cmd)
            resp = ok(rid, data)
            encode(resp)  # valida tamaño antes de encolar
        except CommandError as e:
            resp = fail(rid, e.code, e.message)
        except ProtocolError as e:
            resp = fail(rid, e.code, "respuesta demasiado grande")
        except Exception as e:
            log.exception("handler falló type=%s exc=%s", cmd.TYPE, type(e).__name__)
            resp = fail(rid, "internal", "error interno")
        await self._reply(c, resp)
        return True

    async def _hello(self, c: _Conn, rid: str, cmd: Hello) -> None:
        c.client = cmd.client
        # Registrar antes de leer SQLite: lo posterior queda retenido en `pending`.
        c.pending = deque()
        c.subscribed = True
        await self._send_direct(c, ok(rid, {"v": 1}))
        if self._account is not None:
            await self._send_direct(c, to_envelope(self._account))
        if cmd.since is not None:
            events, truncated = await self._backfill(cmd.since)
            await self._send_direct(c, to_envelope(BackfillStart(since=cmd.since)))
            for m in events:
                c.sent_keys.add((m.chat_jid, m.wa_id))
                await self._send_direct(c, to_envelope(m))
            await self._send_direct(
                c, to_envelope(BackfillEnd(count=len(events), truncated=truncated))
            )
        if c.closing:
            return
        # Sin awaits: ningún broadcast puede intercalarse entre el vaciado y el modo en vivo.
        pending, c.pending = c.pending, None
        for payload, line in pending or ():
            key = _key(payload)
            if key is not None and key in c.sent_keys:
                continue
            self._offer(c, line)
        c.sent_keys.clear()
        if not c.closing:
            c.writer_task = self._spawn(self._writer_loop(c))

    # ---- escritura ----------------------------------------------------------------------

    async def _reply(self, c: _Conn, env: Envelope) -> None:
        """Respuesta: por la cola si el cliente ya está en vivo; directa si aún no."""
        if c.closing:
            return
        if c.writer_task is None:
            await self._send_direct(c, env)
        else:
            self._offer(c, encode(env))

    async def _send_direct(self, c: _Conn, env: Envelope) -> None:
        if c.closing:
            return
        try:
            c.writer.write(encode(env))
            await asyncio.wait_for(c.writer.drain(), self._write_timeout)
        except (ConnectionError, TimeoutError):
            self._abort(c)

    async def _writer_loop(self, c: _Conn) -> None:
        try:
            while True:
                line = await c.queue.get()
                c.writer.write(line)
                await asyncio.wait_for(c.writer.drain(), self._write_timeout)
        except (ConnectionError, TimeoutError):
            self._abort(c)

    def _abort(self, c: _Conn) -> None:
        c.closing = True
        c.writer.transport.abort()

    def _kick(self, c: _Conn, code: str) -> None:
        """Desconecta a un cliente: error final y cierre; no espera a que lea."""
        if c.closing:
            return
        c.closing = True
        if c.writer_task is not None:
            c.writer_task.cancel()
        log.warning("cliente desconectado id=%d code=%s", c.id, code)
        self._spawn(self._farewell(c, code))

    async def _farewell(self, c: _Conn, code: str) -> None:
        try:
            c.writer.write(encode(error_envelope(code, "desconectado")))
            await asyncio.wait_for(c.writer.drain(), _KICK_GRACE)
        except (ConnectionError, TimeoutError):
            pass
        finally:
            c.writer.transport.abort()

    def _spawn(self, coro: Awaitable[None]) -> asyncio.Task[None]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task


async def _remove_stale(path: Path) -> None:
    """Borra un socket huérfano; falla si hay un daemon vivo o no es un socket propio."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
        raise OSError(f"{path.name} existe y no es un socket propio; no se toca")
    try:
        _, w = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 1.0)
    except (ConnectionRefusedError, FileNotFoundError):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()  # noqa: ASYNC240
        return
    except TimeoutError:
        raise AlreadyRunningError("el socket no responde; ¿daemon ocupado?") from None
    w.close()
    raise AlreadyRunningError("ya hay un daemon escuchando en el socket")
