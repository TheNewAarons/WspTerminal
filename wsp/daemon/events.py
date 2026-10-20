"""Eventos de neonize → modelos del protocolo; persistir primero, emitir después del commit.

`translate` es pura (duck-typing sobre los protobuf de neonize, sin importarlo). `Bridge`
persiste cada traducción en el store (único escritor) y solo entonces la emite. Los eventos que
no se pueden traducir se registran por tipo (nunca contenido) y se ignoran.
"""

import logging
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from pydantic import ValidationError

from wsp.protocol.models import (
    ChatUpdated,
    Event,
    MessageDeleted,
    MessageEdited,
    MessageNew,
    MessageStatus,
    Presence,
    Reaction,
)
from wsp.store import repo

log = logging.getLogger(__name__)

STATUS_CHAT: Final = "status@broadcast"
_PROTO_REVOKE: Final = 0
_PROTO_EDIT: Final = 14
_RECEIPT_DELIVERED: Final = 1
_RECEIPT_READ: Final = 4
_WRAPPERS: Final = (
    "ephemeralMessage",
    "viewOnceMessage",
    "viewOnceMessageV2",
    "documentWithCaptionMessage",
    "editedMessage",
)
_MEDIA: Final = {
    "imageMessage": "image",
    "videoMessage": "video",
    "audioMessage": "audio",
    "documentMessage": "document",
    "stickerMessage": "sticker",
    "contactMessage": "contact",
    "locationMessage": "location",
}


class Untranslatable(Exception):
    """El evento no tiene traducción; `kind` es el tipo (sin contenido)."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class Item:
    """Un resultado de traducción: el evento del protocolo más lo necesario para persistirlo."""

    payload: Event
    sender_jid: str | None = None  # autor (ediciones/borrados validan autoría)
    from_me: bool = False
    emit: bool = True  # False: se persiste pero no se emite (p. ej. history sync)
    count_unread: bool = True


@dataclass(frozen=True, slots=True)
class ContactSeen:
    jid: str
    push_name: str


Translated = Item | ContactSeen


@dataclass(frozen=True, slots=True)
class _Content:
    kind: str
    body: str | None
    quoted_wa_id: str | None = None
    edit_target: str | None = None
    revoke_target: str | None = None
    reaction_target: str | None = None


# ---- helpers sobre protobuf ---------------------------------------------------------------


def has(obj: Any, name: str) -> bool:
    """HasField para protobuf; truthiness para dobles de prueba."""
    try:
        return bool(obj.HasField(name))
    except (AttributeError, ValueError, TypeError):
        return bool(getattr(obj, name, None))


def jid_str(jid: Any) -> str | None:
    """JID protobuf → `user@server` (sin número de dispositivo); None si vacío."""
    if jid is None:
        return None
    user = getattr(jid, "User", "") or ""
    server = getattr(jid, "Server", "") or ""
    if not user and not server:
        return None
    return f"{user}@{server}" if user else server


def norm_ts(ts: Any) -> int:
    value = int(ts or 0)
    return value // 1000 if value > 10**11 else value


def _text_of(msg: Any) -> str | None:
    if getattr(msg, "conversation", ""):
        return str(msg.conversation)
    if has(msg, "extendedTextMessage"):
        return str(msg.extendedTextMessage.text or "") or None
    return None


def _unwrap(msg: Any) -> Any:
    for _ in range(4):
        for name in _WRAPPERS:
            if has(msg, name):
                inner = getattr(msg, name).message
                if inner is not None:
                    msg = inner
                    break
        else:
            return msg
    return msg


def _content(raw: Any) -> _Content:
    msg = _unwrap(raw)
    if has(msg, "protocolMessage"):
        proto = msg.protocolMessage
        target = str(getattr(proto.key, "ID", "") or "")
        if proto.type == _PROTO_REVOKE and target:
            return _Content("revoke", None, revoke_target=target)
        if proto.type == _PROTO_EDIT and target and has(proto, "editedMessage"):
            new_text = _text_of(_unwrap(proto.editedMessage))
            if new_text is not None:
                return _Content("edit", new_text, edit_target=target)
        raise Untranslatable("protocolMessage")
    if has(msg, "reactionMessage"):
        r = msg.reactionMessage
        target = str(getattr(r.key, "ID", "") or "")
        if not target:
            raise Untranslatable("reactionMessage")
        return _Content("reaction", str(r.text or ""), reaction_target=target)
    if has(msg, "extendedTextMessage"):
        ext = msg.extendedTextMessage
        quoted = str(getattr(ext.contextInfo, "stanzaID", "") or "") or None
        return _Content("reply" if quoted else "text", str(ext.text or "") or None, quoted)
    if getattr(msg, "conversation", ""):
        return _Content("text", str(msg.conversation))
    for field, kind in _MEDIA.items():
        if has(msg, field):
            caption = str(getattr(getattr(msg, field), "caption", "") or "") or None
            return _Content(kind, caption)
    raise Untranslatable("message")


# ---- traducción por tipo de evento --------------------------------------------------------


def _message(ev: Any, me_jid: str) -> list[Translated]:
    info = ev.Info
    src = info.MessageSource
    chat = jid_str(src.Chat)
    wa_id = str(info.ID or "")
    if not chat or not wa_id:
        raise Untranslatable("message")
    if chat == STATUS_CHAT:  # estados/historias: fuera de alcance
        return []
    from_me = bool(src.IsFromMe)
    ts = norm_ts(info.Timestamp)
    sender = None if from_me else (jid_str(src.Sender) or chat)  # directo: sender = chat (A1)
    c = _content(ev.Message)
    out: list[Translated] = []
    pushname = str(getattr(info, "Pushname", "") or "")
    if pushname and sender and not from_me:
        out.append(ContactSeen(sender, pushname))
    if c.kind == "revoke":
        if c.revoke_target is None:
            raise Untranslatable("message")
        item = Item(MessageDeleted(chat_jid=chat, wa_id=c.revoke_target, deleted_at=ts))
    elif c.kind == "edit":
        if c.edit_target is None or c.body is None:
            raise Untranslatable("message")
        item = Item(MessageEdited(chat_jid=chat, wa_id=c.edit_target, body=c.body, edited_at=ts))
    elif c.kind == "reaction":
        if c.reaction_target is None:
            raise Untranslatable("message")
        item = Item(
            Reaction(
                chat_jid=chat,
                wa_id=c.reaction_target,
                sender_jid=sender or me_jid,
                emoji=c.body or "",
                ts=ts,
            )
        )
    else:
        item = Item(
            MessageNew(
                chat_jid=chat,
                wa_id=wa_id,
                sender_jid=sender,
                from_me=from_me,
                ts=ts,
                kind=c.kind,
                body=c.body,
                quoted_wa_id=c.quoted_wa_id,
                status="sent",
            )
        )
    out.append(
        Item(item.payload, sender_jid=sender, from_me=from_me, count_unread=item.count_unread)
    )
    return out


def _receipt(ev: Any) -> list[Translated]:
    rtype = int(ev.Type)
    status = {_RECEIPT_DELIVERED: "delivered", _RECEIPT_READ: "read"}.get(rtype)
    if status is None:  # incluye READ_SELF/PLAYED: no cambian el estado de envío
        raise Untranslatable("receipt")
    chat = jid_str(ev.MessageSource.Chat)
    if not chat:
        raise Untranslatable("receipt")
    return [
        Item(MessageStatus(chat_jid=chat, wa_id=str(wa_id), status=status))  # type: ignore[arg-type]
        for wa_id in ev.MessageIDs
        if wa_id
    ]


def _presence(ev: Any) -> list[Translated]:
    jid = jid_str(getattr(ev, "From", None))
    if not jid:
        raise Untranslatable("presence")
    if getattr(ev, "Unavailable", False):
        last = norm_ts(getattr(ev, "LastSeen", 0)) or None
        return [Item(Presence(jid=jid, state="unavailable", last_seen=last))]
    return [Item(Presence(jid=jid, state="available"))]


def _chat_presence(ev: Any) -> list[Translated]:
    src = ev.MessageSource
    jid = jid_str(src.Sender) or jid_str(src.Chat)
    state = {1: "composing", 2: "paused"}.get(int(ev.State))
    if not jid or state is None:
        raise Untranslatable("chat_presence")
    return [Item(Presence(jid=jid, state=state))]  # type: ignore[arg-type]


def _group_name(info: Any) -> str | None:
    for attr in ("Name", "GroupName"):
        if has(info, attr):
            name = str(getattr(getattr(info, attr), "Name", "") or "")
            if name:
                return name
    return None


def _group(ev: Any) -> list[Translated]:
    info = getattr(ev, "GroupInfo", ev)  # JoinedGroup envuelve el GroupInfo
    jid = jid_str(getattr(info, "JID", None))
    if not jid:
        raise Untranslatable("group")
    return [Item(ChatUpdated(jid=jid, kind="group", title=_group_name(info)))]


def _history(ev: Any, me_jid: str) -> list[Translated]:
    out: list[Translated] = []
    for conv in ev.Data.conversations:
        chat = str(conv.ID or "")
        if not chat or chat == STATUS_CHAT:
            continue
        title = str(getattr(conv, "name", "") or "") or None
        out.append(Item(ChatUpdated(jid=chat, kind=repo.infer_kind(chat), title=title), emit=False))
        for hm in conv.messages:
            wmi = hm.message
            if not has(wmi, "message"):
                continue
            key = wmi.key
            wa_id = str(key.ID or "")
            if not wa_id:
                continue
            from_me = bool(key.fromMe)
            part = jid_str_from_str(getattr(key, "participant", "") or "")
            sender = None if from_me else (part or chat)
            try:
                c = _content(wmi.message)
            except Untranslatable:
                continue
            if c.kind in {"revoke", "edit", "reaction"}:
                continue
            out.append(
                Item(
                    MessageNew(
                        chat_jid=chat,
                        wa_id=wa_id,
                        sender_jid=sender,
                        from_me=from_me,
                        ts=norm_ts(wmi.messageTimestamp),
                        kind=c.kind,
                        body=c.body,
                        quoted_wa_id=c.quoted_wa_id,
                    ),
                    sender_jid=sender,
                    from_me=from_me,
                    emit=False,
                    count_unread=False,
                )
            )
    return out


def jid_str_from_str(jid: str) -> str | None:
    return jid or None


def translate(kind: str, ev: Any, me_jid: str = "me") -> list[Translated]:
    """Traduce un evento de neonize; `Untranslatable` si no hay traducción."""
    match kind:
        case "MessageEv":
            return _message(ev, me_jid)
        case "ReceiptEv":
            return _receipt(ev)
        case "PresenceEv":
            return _presence(ev)
        case "ChatPresenceEv":
            return _chat_presence(ev)
        case "GroupInfoEv" | "JoinedGroupEv":
            return _group(ev)
        case "HistorySyncEv":
            return _history(ev, me_jid)
        case _:
            raise Untranslatable(kind)


# ---- persistencia + emisión ---------------------------------------------------------------


class Bridge:
    """Traduce, persiste (commit) y recién entonces emite."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        emit: Callable[[Event], None],
        *,
        me_jid: str = "me",
    ) -> None:
        self._conn = conn
        self._emit = emit
        self.me_jid = me_jid
        self.stats: Counter[str] = Counter()  # contadores sin PII

    async def handle(self, kind: str, ev: Any) -> None:
        try:
            items = translate(kind, ev, self.me_jid)
        except Untranslatable as e:
            self.stats["ignored"] += 1
            log.info("evento ignorado type=%s", e.kind)
            return
        except (AttributeError, TypeError, ValueError, ValidationError) as e:
            self.stats["bad_event"] += 1
            log.warning("evento no traducible type=%s exc=%s", kind, type(e).__name__)
            return
        for item in items:
            try:
                changed = self._persist(item)
            except sqlite3.Error as e:
                self.stats["db_error"] += 1
                log.error("persistencia falló type=%s exc=%s", kind, type(e).__name__)
                continue
            if changed and isinstance(item, Item) and item.emit:
                self._emit(item.payload)

    def _persist(self, item: Translated) -> bool:
        """True si hay algo nuevo que emitir. Cada operación del repo hace su propio commit."""
        conn = self._conn
        if isinstance(item, ContactSeen):
            repo.upsert_contact(conn, item.jid, push_name=item.push_name)
            return False
        p = item.payload
        match p:
            case MessageNew():
                res = repo.upsert_message(
                    conn,
                    repo.MessageIn(
                        chat_jid=p.chat_jid,
                        wa_id=p.wa_id,
                        ts=p.ts,
                        from_me=p.from_me,
                        sender_jid=item.sender_jid,
                        kind=p.kind,
                        body=p.body,
                        quoted_wa_id=p.quoted_wa_id,
                        status=p.status,
                    ),
                    count_unread=item.count_unread,
                )
                if not res.applied:
                    self.stats["collision"] += 1
                return res.applied and res.inserted
            case MessageStatus():
                return repo.update_status(conn, p.chat_jid, p.wa_id, p.status, p.error)
            case MessageEdited():
                return repo.apply_edit(
                    conn,
                    p.chat_jid,
                    p.wa_id,
                    p.body,
                    p.edited_at,
                    sender_jid=item.sender_jid,
                    from_me=item.from_me,
                )
            case MessageDeleted():
                return repo.apply_delete(
                    conn,
                    p.chat_jid,
                    p.wa_id,
                    p.deleted_at,
                    sender_jid=item.sender_jid,
                    from_me=item.from_me,
                )
            case Reaction():
                repo.upsert_reaction(conn, p.chat_jid, p.wa_id, p.sender_jid, p.emoji, p.ts)
                return True
            case ChatUpdated():
                repo.upsert_chat(conn, p.jid, kind=p.kind, title=p.title)
                return True
            case Presence():
                return True  # efímera: no se persiste
            case _:
                return False
