"""Conversión entre filas del store y modelos del protocolo (única fuente de verdad)."""

from wsp.protocol.models import ChatUpdated, MessageNew
from wsp.store import repo


def message_event(r: repo.MessageRow) -> MessageNew:
    """Fila → `message.new` (backfill, history, search)."""
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


def message_in(m: MessageNew) -> repo.MessageIn:
    """`message.new` → entrada del store."""
    return repo.MessageIn(
        chat_jid=m.chat_jid,
        wa_id=m.wa_id,
        ts=m.ts,
        from_me=m.from_me,
        sender_jid=m.sender_jid,
        kind=m.kind,
        body=m.body,
        quoted_wa_id=m.quoted_wa_id,
        status=m.status,
    )
