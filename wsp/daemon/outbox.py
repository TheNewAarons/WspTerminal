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
from wsp.protocol.rows import chat_event, message_event, message_in
from wsp.protocol.server import CommandError
from wsp.store import db, repo
from wsp.store.writer import Writer

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
        writer: Writer,
        client: WaClient,
        cfg: OutboxConfig,
        emit: Callable[[Event], None],
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        now: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._writer = writer
        self._client = client
        self._cfg = cfg
        self._emit = emit
        self._sleep = sleep
        self._now = now
        self._bucket = TokenBucket(cfg.rate_per_sec, cfg.burst, clock)
        self._wake = asyncio.Event()
        self._stopping = False
        self._task: asyncio.Task[None] | None = None
        self._inflight: set[asyncio.Task[dict[str, Any]]] = set()

    # ---- encolar ------------------------------------------------------------------------

    async def enqueue(
        self, request_id: str, payload: Mapping[str, Any], chat_jid: str
    ) -> dict[str, Any]:
        """Encola (idempotente por request_id) y devuelve el estado actual del envío.

        Una vez enviado al hilo del writer, la operación (commit + emisión) se completa aunque se
        cancele al llamante (p. ej. timeout del handler): `shield` evita un mensaje persistido y
        encolado pero nunca emitido. `stop()` espera las operaciones en vuelo.
        """
        task = asyncio.ensure_future(self._enqueue(request_id, payload, chat_jid))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        return await asyncio.shield(task)

    async def _enqueue(
        self, request_id: str, payload: Mapping[str, Any], chat_jid: str
    ) -> dict[str, Any]:
        placeholder = self._placeholder(request_id, payload, chat_jid)
        item, created = await self._writer.run(
            self._enqueue_tx, request_id, chat_jid, payload, placeholder
        )
        if created and placeholder is not None:
            self._emit(placeholder)
        if created:
            self._wake.set()
        return self._result(item)

    def _placeholder(
        self, request_id: str, payload: Mapping[str, Any], chat_jid: str
    ) -> MessageNew | None:
        """Mensaje `pending` visible de inmediato (solo texto/respuesta; reacciones no)."""
        if payload["kind"] not in ("text", "reply"):
            return None
        return MessageNew(
            chat_jid=chat_jid,
            wa_id=placeholder_id(request_id),
            from_me=True,
            ts=self._now(),
            kind="text",
            body=payload["text"],
            quoted_wa_id=payload.get("quoted_wa_id"),
            status="pending",
        )

    def _enqueue_tx(
        self,
        conn: sqlite3.Connection,
        request_id: str,
        chat_jid: str,
        payload: Mapping[str, Any],
        placeholder: MessageNew | None,
    ) -> tuple[repo.OutboxItem, bool]:
        """Hilo del writer. (estado actual, True si se creó ahora). Una transacción atómica."""
        prev = repo.outbox_get(conn, request_id)
        if prev is not None:
            return prev, False
        try:
            with db.transaction(conn):
                repo.outbox_put(conn, request_id, chat_jid, payload, self._now())
                if placeholder is not None:
                    repo.upsert_chat(conn, chat_jid)
                    repo.upsert_message(conn, message_in(placeholder), count_unread=False)
        except ValueError:
            raise CommandError("payload_too_big", "mensaje demasiado grande") from None
        except sqlite3.Error as e:
            log.error("encolar falló exc=%s", type(e).__name__)
            raise CommandError("internal", "no se pudo encolar") from None
        item = repo.outbox_get(conn, request_id)
        if item is None:
            raise CommandError("internal", "no se pudo encolar")
        return item, True

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
        """Deja terminar el envío en curso (drain), detiene el worker y espera lo en vuelo."""
        self._stopping = True
        self._wake.set()
        task, self._task = self._task, None
        try:
            if task is not None:
                try:
                    await asyncio.wait_for(task, drain_seconds)
                except TimeoutError:
                    log.warning("outbox: drain agotó el tiempo")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error("outbox terminó con error exc=%s", type(e).__name__)
        finally:
            await self.join()

    async def join(self) -> None:
        """Espera los encolados ya enviados al writer (commit + emisión)."""
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)

    async def _run(self) -> None:
        while not self._stopping:
            # clear ANTES de consultar: un wake durante el salto de hilo no se pierde.
            self._wake.clear()
            item = await self._writer.run(repo.outbox_next)
            if item is None:
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
        await self._on_sent(item, res.wa_id)

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

    async def _on_sent(self, item: repo.OutboxItem, real_id: str) -> None:
        # Si cancelan al worker, el trabajo ya iniciado en el hilo igual hace commit completo.
        try:
            await self._writer.run(self._close_sent, item, real_id)
        except sqlite3.Error as e:
            log.error("cerrar envío falló exc=%s", type(e).__name__)
            return
        if item.payload.get("kind") != "react":
            self._emit(MessageStatus(chat_jid=item.chat_jid, wa_id=real_id, status="sent"))

    def _close_sent(self, conn: sqlite3.Connection, item: repo.OutboxItem, real_id: str) -> None:
        """Hilo del writer; una transacción atómica."""
        chat = item.chat_jid
        with db.transaction(conn):
            if item.payload.get("kind") == "react":
                repo.upsert_reaction(
                    conn, chat, item.payload["wa_id"], "me", item.payload["emoji"], self._now()
                )
            else:
                repo.rebind_wa_id(conn, chat, placeholder_id(item.request_id), real_id)
                repo.update_status(conn, chat, real_id, "sent")
            repo.outbox_done(conn, item.request_id, real_id)

    async def _on_error(
        self, item: repo.OutboxItem, code: str, exc_type: str, *, final: bool = False
    ) -> None:
        log.warning("envío falló attempt=%d code=%s exc=%s", item.attempts, code, exc_type)
        retry = not final and item.attempts < self._cfg.max_attempts and not self._stopping
        try:
            await self._writer.run(self._record_failure, item, code, retry)
        except sqlite3.Error as e:
            log.error("registrar fallo falló exc=%s", type(e).__name__)
            return
        if not retry and item.payload.get("kind") != "react":
            self._emit(
                MessageStatus(
                    chat_jid=item.chat_jid,
                    wa_id=placeholder_id(item.request_id),
                    status="failed",
                    error=code,
                )
            )
        if retry:
            await self._sleep(self._cfg.retry_base * 2 ** (item.attempts - 1))

    @staticmethod
    def _record_failure(
        conn: sqlite3.Connection, item: repo.OutboxItem, code: str, retry: bool
    ) -> None:
        """Hilo del writer: fila de outbox y, si es definitivo, el mensaje placeholder."""
        repo.outbox_fail(conn, item.request_id, code, retry=retry)
        if not retry and item.payload.get("kind") != "react":
            repo.update_status(conn, item.chat_jid, placeholder_id(item.request_id), "failed", code)


# ---- handler de comandos del socket ------------------------------------------------------


class Commands:
    """`Handler` del `Server`: despacha comandos al outbox o al store (SQLite en el hilo writer)."""

    def __init__(self, writer: Writer, outbox: Outbox) -> None:
        self._writer = writer
        self._outbox = outbox

    async def __call__(self, request_id: str, cmd: HandledCommand) -> Mapping[str, Any]:
        try:
            return await self._dispatch(request_id, cmd)
        except ValidationError:
            raise CommandError("internal", "datos inválidos en el store") from None
        except sqlite3.Error as e:
            log.error("comando falló exc=%s", type(e).__name__)
            raise CommandError("internal", "error de almacenamiento") from None

    async def _dispatch(self, rid: str, cmd: HandledCommand) -> Mapping[str, Any]:
        match cmd:
            case SendText():
                return await self._outbox.enqueue(
                    rid, {"kind": "text", "text": cmd.text}, cmd.chat_jid
                )
            case SendReply():
                author = await self._writer.run(repo.message_sender, cmd.chat_jid, cmd.quoted_wa_id)
                payload = {
                    "kind": "reply",
                    "text": cmd.text,
                    "quoted_wa_id": cmd.quoted_wa_id,
                    "quoted_sender": None if author is None else author[0],
                }
                return await self._outbox.enqueue(rid, payload, cmd.chat_jid)
            case React():
                author = await self._writer.run(repo.message_sender, cmd.chat_jid, cmd.wa_id)
                if author is None:
                    raise CommandError("not_found", "mensaje desconocido")
                payload = {
                    "kind": "react",
                    "wa_id": cmd.wa_id,
                    "emoji": cmd.emoji,
                    "author_jid": author[0],
                }
                return await self._outbox.enqueue(rid, payload, cmd.chat_jid)
            case _:
                return await self._writer.run(_store_command, cmd)


def _store_command(conn: sqlite3.Connection, cmd: HandledCommand) -> Mapping[str, Any]:
    """Comandos que solo tocan el store (hilo del writer, incluida la serialización)."""
    match cmd:
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
        case _:  # pragma: no cover - lo despachan SendText/SendReply/React
            raise CommandError("internal", "comando no soportado")
