"""Modelos pydantic v2 y framing JSON-lines del protocolo wsp (v1).

Una línea = un objeto JSON UTF-8 (`Envelope`), máximo `MAX_LINE` bytes sin el `\\n`.
Los errores de decodificación no incluyen contenido del mensaje (sin PII).
"""

import asyncio
import json
from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

PROTOCOL_VERSION: Final = 1
MAX_LINE: Final = 1_048_576  # 1 MiB por línea (sin el salto)
MAX_TEXT: Final = 65_536
MAX_LIMIT: Final = 1000

Jid = Annotated[str, StringConstraints(min_length=1, max_length=128)]
WaId = Annotated[str, StringConstraints(min_length=1, max_length=128)]
RequestId = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
]
Text = Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT)]
Ts = Annotated[int, Field(ge=0, le=2**63 - 1)]
Limit = Annotated[int, Field(ge=1, le=MAX_LIMIT)]

Status = Literal["pending", "sent", "delivered", "read", "failed"]
ChatKind = Literal["direct", "group", "broadcast"]


class ProtocolError(Exception):
    """Error de framing/validación; `code` es estable, `message` no contiene contenido."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class _Model(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, ser_json_inf_nan="constants"
    )


class Envelope(_Model):
    v: Literal[1] = PROTOCOL_VERSION
    type: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    request_id: RequestId | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class Payload(_Model):
    """Base de `data`; cada subclase fija su `TYPE` de envelope."""

    TYPE: ClassVar[str]


# ---- Eventos daemon → cliente -------------------------------------------------------------


class MessageNew(Payload):
    TYPE: ClassVar[str] = "message.new"
    chat_jid: Jid
    wa_id: WaId
    sender_jid: Jid | None = None
    from_me: bool = False
    ts: Ts
    kind: Annotated[str, StringConstraints(min_length=1, max_length=32)] = "text"
    body: Annotated[str, StringConstraints(max_length=MAX_TEXT)] | None = None
    quoted_wa_id: WaId | None = None
    status: Status = "sent"
    error: Annotated[str, StringConstraints(max_length=200)] | None = None
    edited_at: Ts | None = None
    deleted_at: Ts | None = None


class MessageStatus(Payload):
    TYPE: ClassVar[str] = "message.status"
    chat_jid: Jid
    wa_id: WaId
    status: Status
    error: Annotated[str, StringConstraints(max_length=200)] | None = None


class MessageEdited(Payload):
    TYPE: ClassVar[str] = "message.edited"
    chat_jid: Jid
    wa_id: WaId
    body: Annotated[str, StringConstraints(max_length=MAX_TEXT)]
    edited_at: Ts


class MessageDeleted(Payload):
    TYPE: ClassVar[str] = "message.deleted"
    chat_jid: Jid
    wa_id: WaId
    deleted_at: Ts


class Reaction(Payload):
    TYPE: ClassVar[str] = "reaction"
    chat_jid: Jid
    wa_id: WaId
    sender_jid: Jid
    emoji: Annotated[str, StringConstraints(max_length=32)]  # "" = reacción retirada
    ts: Ts


class ChatUpdated(Payload):
    TYPE: ClassVar[str] = "chat.updated"
    jid: Jid
    kind: ChatKind
    title: Annotated[str, StringConstraints(max_length=256)] | None = None
    unread_count: Ts = 0
    last_message_at: Ts | None = None
    archived: bool = False
    muted_until: Ts = 0
    pinned: bool = False


class Presence(Payload):
    TYPE: ClassVar[str] = "presence"
    jid: Jid
    state: Literal["available", "unavailable", "composing", "paused"]
    last_seen: Ts | None = None


class AccountState(Payload):
    TYPE: ClassVar[str] = "account.state"
    state: Literal["pairing", "connected", "disconnected", "logged_out"]
    reason: Annotated[str, StringConstraints(max_length=200)] | None = None


class BackfillStart(Payload):
    TYPE: ClassVar[str] = "backfill.start"
    since: Ts


class BackfillEnd(Payload):
    TYPE: ClassVar[str] = "backfill.end"
    count: Ts
    truncated: bool = False  # hubo más mensajes que el tope: reconectar con el último ts


class ErrorEvent(Payload):
    TYPE: ClassVar[str] = "error"
    code: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    message: Annotated[str, StringConstraints(max_length=200)]


Event = (
    MessageNew
    | MessageStatus
    | MessageEdited
    | MessageDeleted
    | Reaction
    | ChatUpdated
    | Presence
    | AccountState
    | BackfillStart
    | BackfillEnd
    | ErrorEvent
)

# ---- Comandos cliente → daemon ------------------------------------------------------------


class Hello(Payload):
    TYPE: ClassVar[str] = "hello"
    since: Ts | None = None  # inclusivo: el cliente deduplica por (chat_jid, wa_id)
    client: Literal["cli", "tail"]


class SendText(Payload):
    TYPE: ClassVar[str] = "send_text"
    chat_jid: Jid
    text: Text


class SendReply(Payload):
    TYPE: ClassVar[str] = "send_reply"
    chat_jid: Jid
    quoted_wa_id: WaId
    text: Text


class React(Payload):
    TYPE: ClassVar[str] = "react"
    chat_jid: Jid
    wa_id: WaId
    emoji: Annotated[str, StringConstraints(max_length=32)]


class MarkRead(Payload):
    TYPE: ClassVar[str] = "mark_read"
    chat_jid: Jid


class ListChats(Payload):
    TYPE: ClassVar[str] = "list_chats"
    limit: Limit


class History(Payload):
    TYPE: ClassVar[str] = "history"
    chat_jid: Jid
    n: Limit


class Search(Payload):
    TYPE: ClassVar[str] = "search"
    q: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    limit: Limit


class Mute(Payload):
    TYPE: ClassVar[str] = "mute"
    chat_jid: Jid
    until: Ts  # ts Unix; 0 = quitar silencio


class Ping(Payload):
    TYPE: ClassVar[str] = "ping"


HandledCommand = SendText | SendReply | React | MarkRead | ListChats | History | Search | Mute
Command = Hello | Ping | HandledCommand

EVENT_CLASSES: Final[tuple[type[Event], ...]] = (
    MessageNew,
    MessageStatus,
    MessageEdited,
    MessageDeleted,
    Reaction,
    ChatUpdated,
    Presence,
    AccountState,
    BackfillStart,
    BackfillEnd,
    ErrorEvent,
)
COMMAND_CLASSES: Final[tuple[type[Command], ...]] = (
    Hello,
    Ping,
    SendText,
    SendReply,
    React,
    MarkRead,
    ListChats,
    History,
    Search,
    Mute,
)
_EVENTS: Final[dict[str, type[Event]]] = {c.TYPE: c for c in EVENT_CLASSES}
_COMMANDS: Final[dict[str, type[Command]]] = {c.TYPE: c for c in COMMAND_CLASSES}


# ---- Respuestas ---------------------------------------------------------------------------


def to_envelope(payload: Payload, request_id: str | None = None) -> Envelope:
    return Envelope(type=payload.TYPE, request_id=request_id, data=payload.model_dump(mode="json"))


def ok(request_id: str, data: Mapping[str, Any] | None = None) -> Envelope:
    return Envelope(type="ok", request_id=request_id, data=dict(data or {}))


def fail(request_id: str, code: str, message: str) -> Envelope:
    return Envelope(
        type="fail", request_id=request_id, data={"code": code, "message": message[:200]}
    )


def error_envelope(code: str, message: str) -> Envelope:
    return to_envelope(ErrorEvent(code=code, message=message[:200]))


# ---- Framing JSON-lines -------------------------------------------------------------------


def _summarize(exc: ValidationError) -> str:
    """Resumen sin valores de entrada: solo ruta y tipo de cada error."""
    parts = [f"{'.'.join(str(p) for p in e['loc']) or '-'}: {e['type']}" for e in exc.errors()]
    return "; ".join(parts[:5])


def _reject_constant(name: str) -> Any:
    raise ValueError(name)


def encode(env: Envelope) -> bytes:
    """Serializa a una línea terminada en `\\n`; rechaza si supera `MAX_LINE`."""
    obj = env.model_dump(mode="json")
    if obj["request_id"] is None:
        del obj["request_id"]
    try:
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    except (ValueError, UnicodeError):  # NaN/Infinity o surrogates sueltos
        raise ProtocolError("bad_encoding", "no se pudo serializar") from None
    if len(body) > MAX_LINE:
        raise ProtocolError("line_too_big", "mensaje demasiado grande")
    return body + b"\n"


def decode_line(line: bytes) -> Envelope:
    """Decodifica una línea; lanza `ProtocolError` con código estable si es inválida."""
    body = line.removesuffix(b"\n")
    if len(body) > MAX_LINE:
        raise ProtocolError("line_too_big", "línea demasiado grande")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise ProtocolError("bad_encoding", "la línea no es UTF-8 válido") from None
    try:
        obj = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise ProtocolError("bad_json", "JSON inválido") from None
    if not isinstance(obj, dict):
        raise ProtocolError("bad_envelope", "se esperaba un objeto JSON")
    if obj.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported_version", f"versión soportada: {PROTOCOL_VERSION}")
    try:
        return Envelope.model_validate(obj)
    except ValidationError as exc:
        raise ProtocolError("bad_envelope", _summarize(exc)) from None


async def read_line(reader: asyncio.StreamReader) -> bytes | None:
    """Lee una línea completa; None en EOF. Lanza `ProtocolError(line_too_big)` si excede."""
    try:
        return await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError:
        return None
    except asyncio.LimitOverrunError:
        raise ProtocolError("line_too_big", "línea demasiado grande") from None


def _validate[T: Payload](cls: type[T], env: Envelope) -> T:
    try:
        return cls.model_validate(env.data)
    except ValidationError as exc:
        raise ProtocolError("bad_payload", _summarize(exc)) from None


def parse_event(env: Envelope) -> Event:
    cls = _EVENTS.get(env.type)
    if cls is None:
        raise ProtocolError("unknown_type", "tipo de evento desconocido")
    return _validate(cls, env)


def parse_command(env: Envelope) -> tuple[str, Command]:
    """Devuelve (request_id, comando); todo comando exige request_id."""
    cls = _COMMANDS.get(env.type)
    if cls is None:
        raise ProtocolError("unknown_type", "comando desconocido")
    if env.request_id is None:
        raise ProtocolError("missing_request_id", "falta request_id")
    return env.request_id, _validate(cls, env)
