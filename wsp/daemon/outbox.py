"""Cola de envíos: comandos del socket → tabla `outbox` → worker con token bucket.

Un envío crea un mensaje placeholder (`wa_id = "wsp:<request_id>"`, estado `pending`) y una fila en
`outbox`. El worker envía por WhatsApp, reasigna el wa_id real (`rebind_wa_id`) y emite
`message.status`. Un `request_id` repetido devuelve el resultado previo sin reenviar.
Los errores se publican como códigos estables, nunca como texto libre.
"""

import asyncio
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

from pydantic import ValidationError

from wsp.daemon.config import OutboxConfig
from wsp.daemon.session import WaClient
from wsp.protocol.models import (
    ChatUpdated,
    Event,
    HandledCommand,
    History,
    ListChats,
    MarkRead,
    MessageNew,
    MessageStatus,
    Mute,
    React,
    Search,
    SendReply,
    SendText,
)
from wsp.protocol.server import CommandError
from wsp.store import db, repo

log = logging.getLogger(__name__)

PLACEHOLDER_PREFIX: Final = "wsp:"
ERR_SEND_FAILED: Final = "send_failed"
ERR_TIMEOUT: Final = "send_timeout"
ERR_INTERRUPTED: Final = "interrupted"
SEND_TIMEOUT: Final = 30.0
IDLE_POLL: Final = 5.0

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class TokenBucket:
    """Token bucket: `rate` tokens/s sostenidos, ráfaga máxima `burst`. Reloj inyectable."""

    def __init__(self, rate: float, burst: int, clock: Clock = time.monotonic) -> None:
        self._rate = rate
        self._burst = float(burst)
        self._clock = clock
        self._tokens = float(burst)
        self._at = clock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._burst, self._tokens + (now - self._at) * self._rate)
        self._at = now

    def take(self) -> float:
        """Consume un token y devuelve 0; si no hay, devuelve los segundos a esperar."""
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0
        return (1.0 - self._tokens) / self._rate

    async def acquire(self, sleep: Sleep = asyncio.sleep) -> None:
        while (wait := self.take()) > 0:
            await sleep(wait)


def placeholder_id(request_id: str) -> str:
    return PLACEHOLDER_PREFIX + request_id


def _error_code(exc: BaseException) -> str:
    return ERR_TIMEOUT if isinstance(exc, TimeoutError) else ERR_SEND_FAILED


class Outbox:
    """Encola envíos y los procesa en orden bajo el token bucket."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        client: WaClient,
        cfg: OutboxConfig,
        emit: Callable[[Event], None],
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        now: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._conn = conn
        self._client = client
        self._cfg = cfg
        self._emit = emit
        self._sleep = sleep
        self._now = now
        self._bucket = TokenBucket(cfg.rate_per_sec, cfg.burst, clock)
        self._wake = asyncio.Event()
        self._stopping = False
        self._task: asyncio.Task[None] | None = None

    # ---- encolar ------------------------------------------------------------------------

    def enqueue(self, request_id: str, payload: Mapping[str, Any], chat_jid: str) -> dict[str, Any]:
        """Encola (idempotente por request_id) y devuelve el estado actual del envío."""
        prev = repo.outbox_get(self._conn, request_id)
        if prev is not None:
            return self._result(prev)
        kind = payload["kind"]
        try:
            with db.transaction(self._conn):
                repo.outbox_put(self._conn, request_id, chat_jid, payload, self._now())
                if kind in ("text", "reply"):
                    repo.upsert_chat(self._conn, chat_jid)
                    repo.upsert_message(
                        self._conn,
                        repo.MessageIn(
                            chat_jid=chat_jid,
                            wa_id=placeholder_id(request_id),
                            ts=self._now(),
                            from_me=True,
                            kind="text",
                            body=payload["text"],
                            quoted_wa_id=payload.get("quoted_wa_id"),
                            status="pending",
                        ),
                        count_unread=False,
                    )
        except ValueError:
            raise CommandError("payload_too_big", "mensaje demasiado grande") from None
        except sqlite3.Error as e:
            log.error("encolar falló exc=%s", type(e).__name__)
            raise CommandError("internal", "no se pudo encolar") from None
        if kind in ("text", "reply"):
            self._emit(
                MessageNew(
                    chat_jid=chat_jid,
                    wa_id=placeholder_id(request_id),
                    from_me=True,
                    ts=self._now(),
                    kind="text",
                    body=payload["text"],
                    quoted_wa_id=payload.get("quoted_wa_id"),
                    status="pending",
                )
            )
        self._wake.set()
        item = repo.outbox_get(self._conn, request_id)
        if item is None:
            raise CommandError("internal", "no se pudo encolar")
        return self._result(item)

    @staticmethod
    def _result(item: repo.OutboxItem) -> dict[str, Any]:
        out: dict[str, Any] = {
            "state": item.state,
            "wa_id": item.wa_id or placeholder_id(item.request_id),
        }
        if item.state == "failed":
            out["error"] = item.error or ERR_SEND_FAILED
        return out

    # ---- worker -------------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="outbox")

    async def stop(self, drain_seconds: float = 10.0) -> None:
        """Deja terminar el envío en curso (drain) y detiene el worker."""
        self._stopping = True
        self._wake.set()
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, drain_seconds)
        except TimeoutError:
            log.warning("outbox: drain agotó el tiempo")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("outbox terminó con error exc=%s", type(e).__name__)

    async def _run(self) -> None:
        while not self._stopping:
            item = repo.outbox_next(self._conn)
            if item is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), IDLE_POLL)
                except TimeoutError:
                    pass
                continue
            await self.process(item)

    async def process(self, item: repo.OutboxItem) -> None:
        """Envía un elemento: éxito → sent; error → reintento con backoff o failed."""
        await self._bucket.acquire(self._sleep)
        try:
            res = await asyncio.wait_for(self._send(item), SEND_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Un timeout pudo haber enviado el mensaje: no se reintenta (evita duplicados).
            await self._on_error(
                item, _error_code(e), type(e).__name__, final=isinstance(e, TimeoutError)
            )
            return
        self._on_sent(item, res.wa_id)

    async def _send(self, item: repo.OutboxItem) -> Any:
        p = item.payload
        match p.get("kind"):
            case "text" | "reply":
                return await self._client.send_text(
                    item.chat_jid, p["text"], p.get("quoted_wa_id"), p.get("quoted_sender")
                )
            case "react":
                return await self._client.send_reaction(
                    item.chat_jid, p["wa_id"], p.get("author_jid"), p["emoji"]
                )
            case _:
                raise ValueError("kind")

    def _on_sent(self, item: repo.OutboxItem, real_id: str) -> None:
        chat = item.chat_jid
        old = placeholder_id(item.request_id)
        try:
            with db.transaction(self._conn):
                if item.payload.get("kind") == "react":
                    repo.upsert_reaction(
                        self._conn,
                        chat,
                        item.payload["wa_id"],
                        "me",
                        item.payload["emoji"],
                        self._now(),
                    )
                else:
                    repo.rebind_wa_id(self._conn, chat, old, real_id)
                    repo.update_status(self._conn, chat, real_id, "sent")
                repo.outbox_done(self._conn, item.request_id, real_id)
        except sqlite3.Error as e:
            log.error("cerrar envío falló exc=%s", type(e).__name__)
            return
        if item.payload.get("kind") == "react":
            return
        self._emit(MessageStatus(chat_jid=chat, wa_id=real_id, status="sent"))

    async def _on_error(
        self, item: repo.OutboxItem, code: str, exc_type: str, *, final: bool = False
    ) -> None:
        log.warning("envío falló attempt=%d code=%s exc=%s", item.attempts, code, exc_type)
        retry = not final and item.attempts < self._cfg.max_attempts and not self._stopping
        try:
            repo.outbox_fail(self._conn, item.request_id, code, retry=retry)
            if not retry:
                self._mark_failed(item, code)
        except sqlite3.Error as e:
            log.error("registrar fallo falló exc=%s", type(e).__name__)
            return
        if retry:
            await self._sleep(self._cfg.retry_base * 2 ** (item.attempts - 1))

    def _mark_failed(self, item: repo.OutboxItem, code: str) -> None:
        if item.payload.get("kind") == "react":
            return
        wa_id = placeholder_id(item.request_id)
        repo.update_status(self._conn, item.chat_jid, wa_id, "failed", code)
        self._emit(MessageStatus(chat_jid=item.chat_jid, wa_id=wa_id, status="failed", error=code))


# ---- handler de comandos del socket ------------------------------------------------------


def message_event(r: repo.MessageRow) -> MessageNew:
    return MessageNew(
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


def chat_event(c: repo.ChatRow) -> ChatUpdated:
    return ChatUpdated(
        jid=c.jid,
        kind=c.kind,
        title=c.title,
        unread_count=c.unread_count,
        last_message_at=c.last_message_at,
        archived=c.archived,
        muted_until=c.muted_until,
        pinned=c.pinned,
    )


class Commands:
    """`Handler` del `Server`: despacha comandos al outbox o al store."""

    def __init__(self, conn: sqlite3.Connection, outbox: Outbox) -> None:
        self._conn = conn
        self._outbox = outbox

    async def __call__(self, request_id: str, cmd: HandledCommand) -> Mapping[str, Any]:
        try:
            return self._dispatch(request_id, cmd)
        except ValidationError:
            raise CommandError("internal", "datos inválidos en el store") from None
        except sqlite3.Error as e:
            log.error("comando falló exc=%s", type(e).__name__)
            raise CommandError("internal", "error de almacenamiento") from None

    def _dispatch(self, rid: str, cmd: HandledCommand) -> Mapping[str, Any]:
        conn = self._conn
        match cmd:
            case SendText():
                return self._outbox.enqueue(rid, {"kind": "text", "text": cmd.text}, cmd.chat_jid)
            case SendReply():
                payload = {
                    "kind": "reply",
                    "text": cmd.text,
                    "quoted_wa_id": cmd.quoted_wa_id,
                    "quoted_sender": self._sender_of(cmd.chat_jid, cmd.quoted_wa_id),
                }
                return self._outbox.enqueue(rid, payload, cmd.chat_jid)
            case React():
                if not self._exists(cmd.chat_jid, cmd.wa_id):
                    raise CommandError("not_found", "mensaje desconocido")
                payload = {
                    "kind": "react",
                    "wa_id": cmd.wa_id,
                    "emoji": cmd.emoji,
                    "author_jid": self._sender_of(cmd.chat_jid, cmd.wa_id),
                }
                return self._outbox.enqueue(rid, payload, cmd.chat_jid)
            case MarkRead():
                return {"changed": repo.mark_read(conn, cmd.chat_jid)}
            case ListChats():
                chats = repo.list_chats(conn, cmd.limit)
                return {"chats": [chat_event(c).model_dump(mode="json") for c in chats]}
            case History():
                rows = repo.last_messages(conn, cmd.chat_jid, cmd.n)
                return {"messages": [message_event(r).model_dump(mode="json") for r in rows]}
            case Search():
                rows = repo.search(conn, cmd.q, cmd.limit)
                return {"messages": [message_event(r).model_dump(mode="json") for r in rows]}
            case Mute():
                repo.upsert_chat(conn, cmd.chat_jid, muted_until=cmd.until)
                return {"muted_until": cmd.until}

    def _exists(self, chat_jid: str, wa_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM messages WHERE chat_jid = ? AND wa_id = ? LIMIT 1", (chat_jid, wa_id)
        ).fetchone()
        return row is not None

    def _sender_of(self, chat_jid: str, wa_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT sender_jid FROM messages WHERE chat_jid = ? AND wa_id = ? LIMIT 1",
            (chat_jid, wa_id),
        ).fetchone()
        return None if row is None else row["sender_jid"]
