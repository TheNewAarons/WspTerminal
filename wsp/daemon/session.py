"""Sesión de WhatsApp: lock de instancia única, archivos 600, supervisión y reconexión.

`Session` no conoce neonize: habla con un `WaClient` (puerto). `NeonizeClient` es el adaptador
real (importa neonize de forma perezosa); los tests usan un cliente falso.
"""

import asyncio
import contextlib
import fcntl
import logging
import os
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from wsp import paths
from wsp.daemon.config import ReconnectConfig
from wsp.daemon.ts import norm_ts
from wsp.protocol.models import AccountState

log = logging.getLogger(__name__)

EXIT_ALREADY_RUNNING: Final = 2
LOCK_NAME: Final = "wspd.lock"
_SESSION_SUFFIXES: Final = ("", "-wal", "-shm", "-journal")
_STATE_LABELS: Final = ("pairing", "connected", "disconnected", "logged_out")

EventSink = Callable[[str, Any], Awaitable[None]]
"""(tipo de evento neonize, evento) → traducción/persistencia (events.py)."""
QrHandler = Callable[[bytes], Awaitable[None]]


class AlreadyRunningError(Exception):
    """Otro `wspd` (o `wsp pair`) tiene el lock de instancia única."""


class InstanceLock:
    """`flock` exclusivo y no bloqueante sobre un archivo del directorio de estado."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.state_dir() / LOCK_NAME
        self._fd: int | None = None

    def acquire(self) -> None:
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise AlreadyRunningError("ya hay un wspd (o wsp pair) en ejecución") from None
        except BaseException:
            os.close(fd)
            raise
        os.fchmod(fd, 0o600)
        self._fd = fd

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


@dataclass(frozen=True, slots=True)
class SendResult:
    wa_id: str
    ts: int


class WaClient(Protocol):
    """Lo mínimo que el daemon necesita de WhatsApp."""

    def bind(self, on_event: EventSink, on_qr: QrHandler) -> None: ...
    async def connect(self) -> None: ...
    async def wait_closed(self) -> None:
        """Vuelve (o lanza) cuando la conexión terminó."""

    async def disconnect(self) -> None: ...
    async def send_text(
        self, jid: str, text: str, quoted_wa_id: str | None = None, quoted_sender: str | None = None
    ) -> SendResult: ...
    async def send_reaction(
        self, chat_jid: str, wa_id: str, author_jid: str | None, emoji: str
    ) -> SendResult: ...
    async def pair_phone(self, phone: str) -> str: ...


def secure_session_files(path: Path | None = None) -> None:
    """Fuerza 600 en la DB de sesión y sus archivos auxiliares (si existen)."""
    base = path or paths.session_path()
    for suffix in _SESSION_SUFFIXES:
        with contextlib.suppress(FileNotFoundError):
            os.chmod(f"{base}{suffix}", 0o600, follow_symlinks=False)


def prepare_session_file(path: Path | None = None) -> Path:
    """Crea la DB de sesión vacía en 600 antes de que la abra neonize."""
    target = path or paths.session_path()
    fd = os.open(target, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    secure_session_files(target)
    return target


class Backoff:
    """Backoff exponencial con jitter; azar inyectable para tests."""

    def __init__(self, cfg: ReconnectConfig, rand: Callable[[], float] = random.random) -> None:
        self._cfg = cfg
        self._rand = rand
        self._attempt = 0

    def next(self) -> float:
        raw = min(self._cfg.cap, self._cfg.base * (2**self._attempt))
        self._attempt = min(self._attempt + 1, 32)
        spread = 1 + self._cfg.jitter * (2 * self._rand() - 1)
        return float(max(0.0, raw * spread))

    def reset(self) -> None:
        self._attempt = 0


_CONNECTION_REASONS: Final = {
    "StreamReplacedEv": "stream_replaced",
    "ClientOutdatedEv": "client_outdated",
    "TemporaryBanEv": "temporary_ban",
    "ConnectFailureEv": "connect_failed",
    "KeepAliveTimeoutEv": "keepalive_timeout",
}


class Session:
    """Supervisa la conexión: reconecta con backoff y publica `account.state` en cada cambio."""

    def __init__(
        self,
        client: WaClient,
        reconnect: ReconnectConfig,
        emit_state: Callable[[AccountState], None],
        sink: EventSink,
        *,
        rand: Callable[[], float] = random.random,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        session_file: Path | None = None,
    ) -> None:
        self._client = client
        self._emit_state = emit_state
        self._sink = sink
        self._sleep = sleep
        self._backoff = Backoff(reconnect, rand)
        self._session_file = session_file
        self._state: tuple[str, str | None] | None = None
        self._stopping = False
        self._logged_out = False
        self._wake = asyncio.Event()
        self._client.bind(self.on_event, self._on_qr)

    @property
    def state(self) -> str | None:
        return None if self._state is None else self._state[0]

    @property
    def client(self) -> WaClient:
        return self._client

    def set_state(self, state: str, reason: str | None = None) -> None:
        """Publica el estado solo si cambió."""
        if state not in _STATE_LABELS or self._state == (state, reason):
            return
        self._state = (state, reason)
        self._emit_state(AccountState(state=state, reason=reason))  # type: ignore[arg-type]

    async def _on_qr(self, _data: bytes) -> None:
        self.set_state("pairing")

    async def on_event(self, kind: str, ev: Any) -> None:
        """Entrada de eventos del cliente: conexión aquí; el resto al `sink`."""
        try:
            if kind == "ConnectedEv":
                self._backoff.reset()
                self.set_state("connected")
            elif kind == "DisconnectedEv":
                self.set_state("disconnected", "closed")
            elif kind == "LoggedOutEv":
                self._logged_out = True
                self.set_state("logged_out")
                self._wake.set()
            elif kind in _CONNECTION_REASONS:
                self.set_state("disconnected", _CONNECTION_REASONS[kind])
            else:
                await self._sink(kind, ev)
        except Exception as e:  # los callbacks de neonize tragan excepciones: solo el tipo
            log.error("evento falló kind=%s exc=%s", kind, type(e).__name__)

    async def run(self) -> None:
        """Bucle de supervisión hasta `stop()` o cierre de sesión (logged_out)."""
        while not self._stopping and not self._logged_out:
            try:
                await self._client.connect()
                secure_session_files(self._session_file)
                await self._client.wait_closed()
                reason = "closed"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                reason = "connect_failed"
                log.warning("conexión falló exc=%s", type(e).__name__)
            if self._stopping or self._logged_out:
                break
            self.set_state("disconnected", reason)
            delay = self._backoff.next()
            log.info("reconectando delay=%.1f", delay)
            await self._sleep_interruptible(delay)

    async def _sleep_interruptible(self, delay: float) -> None:
        sleeper = asyncio.ensure_future(self._sleep(delay))
        waker = asyncio.ensure_future(self._wake.wait())
        try:
            await asyncio.wait({sleeper, waker}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (sleeper, waker):
                t.cancel()
            await asyncio.gather(sleeper, waker, return_exceptions=True)

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        with contextlib.suppress(Exception):
            await self._client.disconnect()


# ---- adaptador real -----------------------------------------------------------------------

_NEONIZE_EVENTS: Final = (
    "MessageEv",
    "ReceiptEv",
    "PresenceEv",
    "ChatPresenceEv",
    "GroupInfoEv",
    "JoinedGroupEv",
    "HistorySyncEv",
    "PushNameEv",
    "ConnectedEv",
    "DisconnectedEv",
    "LoggedOutEv",
    "StreamReplacedEv",
    "ClientOutdatedEv",
    "TemporaryBanEv",
    "ConnectFailureEv",
    "KeepAliveTimeoutEv",
    "PairStatusEv",
)


def split_jid(jid: str) -> tuple[str, str]:
    user, _, server = jid.partition("@")
    return user, server or "s.whatsapp.net"


class NeonizeClient:
    """Adaptador sobre `neonize.aioze.NewAClient` (import perezoso: requiere libmagic/ffmpeg)."""

    def __init__(self, session_file: Path | None = None) -> None:
        from neonize.aioze.client import NewAClient

        self._path = prepare_session_file(session_file)
        self._c = NewAClient(str(self._path))
        self._task: asyncio.Future[Any] | None = None

    def bind(self, on_event: EventSink, on_qr: QrHandler) -> None:
        from neonize.aioze import events as ev

        for name in _NEONIZE_EVENTS:
            cls = getattr(ev, name, None)
            if cls is None:  # versión de neonize sin ese evento
                continue
            self._c.event(cls)(self._forward(name, on_event))

        async def qr(_client: Any, data: bytes) -> None:
            await on_qr(data)

        self._c.qr(qr)

    @staticmethod
    def _forward(name: str, on_event: EventSink) -> Callable[[Any, Any], Awaitable[None]]:
        async def cb(_client: Any, event: Any) -> None:
            await on_event(name, event)

        return cb

    async def connect(self) -> None:
        self._task = await self._c.connect()

    async def wait_closed(self) -> None:
        if self._task is not None:
            await self._task

    async def disconnect(self) -> None:
        await self._c.stop()

    def _jid(self, jid: str) -> Any:
        from neonize.utils import build_jid

        user, server = split_jid(jid)
        return build_jid(user, server)

    async def send_text(
        self, jid: str, text: str, quoted_wa_id: str | None = None, quoted_sender: str | None = None
    ) -> SendResult:
        target = self._jid(jid)
        if quoted_wa_id is None:
            resp = await self._c.send_message(target, text)
        else:
            from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import (
                ContextInfo,
                ExtendedTextMessage,
                Message,
            )

            ctx = ContextInfo(stanzaID=quoted_wa_id, participant=quoted_sender or "")
            msg = Message(extendedTextMessage=ExtendedTextMessage(text=text, contextInfo=ctx))
            resp = await self._c.send_message(target, msg)
        return SendResult(wa_id=str(resp.ID), ts=norm_ts(resp.Timestamp))

    async def send_reaction(
        self, chat_jid: str, wa_id: str, author_jid: str | None, emoji: str
    ) -> SendResult:
        chat = self._jid(chat_jid)
        sender = self._jid(author_jid) if author_jid else self._c.me.JID
        msg = await self._c.build_reaction(chat, sender, wa_id, emoji)
        resp = await self._c.send_message(chat, msg)
        return SendResult(wa_id=str(resp.ID), ts=norm_ts(resp.Timestamp))

    async def pair_phone(self, phone: str) -> str:
        return str(await self._c.PairPhone(phone, True))
