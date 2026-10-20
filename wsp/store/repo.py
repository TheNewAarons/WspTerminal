"""Operaciones de datos. Todas reciben la conexión explícita; SQL siempre parametrizado.

Las escrituras solo deben invocarse con la conexión del daemon. Timestamps en segundos Unix.
"""

import json
import re
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from wsp.store.db import transaction

Status = Literal["pending", "sent", "delivered", "read", "failed"]
ChatKind = Literal["direct", "group", "broadcast"]

DEFAULT_RETENTION_DAYS = 365
"""Política de retención sugerida: el daemon llama purge_older_than(DEFAULT_RETENTION_DAYS)."""

_DAY = 86_400
MAX_LIMIT = 1000
"""Tope de filas devueltas por cualquier consulta de lectura."""
MAX_BODY = 65_536
"""Longitud máxima (caracteres) de body; el exceso se trunca al escribir."""
MAX_FTS_TOKENS = 16
MAX_FTS_TOKEN_LEN = 64
"""Longitud máxima de cada token FTS; un token recortado se busca como prefijo."""
MAX_PAYLOAD_BYTES = 65_536
"""Tamaño máximo (bytes UTF-8 del JSON) del payload de outbox; el exceso se rechaza."""
STUCK_SENDING_SECONDS = 86_400
"""Outbox en 'sending' más antiguo que esto (por created_at) se considera atascado y se purga."""

_ERROR_MAX = 200
_TOKEN_RE = re.compile(r"\w+")
_JID_RE = re.compile(r"\d[\d:._-]*@\S+")
_DIGITS_RE = re.compile(r"\d{7,}")
_MSG_COLS = (
    "m.id, m.chat_jid, m.wa_id, m.sender_jid, m.from_me, m.ts, m.kind, "
    "CASE WHEN m.deleted_at IS NOT NULL THEN NULL ELSE m.body END AS body, "
    "CASE WHEN m.deleted_at IS NOT NULL THEN NULL ELSE m.quoted_wa_id END AS quoted_wa_id, "
    "m.status, m.error, m.edited_at, m.deleted_at"
)


@dataclass(frozen=True, slots=True)
class MessageIn:
    chat_jid: str
    wa_id: str
    ts: int
    from_me: bool = False
    sender_jid: str | None = None
    kind: str | None = None
    body: str | None = None
    quoted_wa_id: str | None = None
    status: Status | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class UpsertResult:
    """applied=False: colisión de wa_id con otro autor; no se modificó nada (id = existente)."""

    id: int
    inserted: bool
    applied: bool = True


@dataclass(frozen=True, slots=True)
class MessageRow:
    id: int
    chat_jid: str
    wa_id: str
    sender_jid: str | None
    from_me: bool
    ts: int
    kind: str
    body: str | None
    quoted_wa_id: str | None
    status: Status
    error: str | None
    edited_at: int | None
    deleted_at: int | None


@dataclass(frozen=True, slots=True)
class ChatRow:
    jid: str
    kind: ChatKind
    title: str | None
    unread_count: int
    last_message_at: int | None
    archived: bool
    muted_until: int
    pinned: bool


@dataclass(frozen=True, slots=True)
class OutboxItem:
    request_id: str
    chat_jid: str
    payload: dict[str, Any]
    state: str
    attempts: int
    error: str | None
    created_at: int
    wa_id: str | None = None


@dataclass(frozen=True, slots=True)
class PurgeResult:
    messages: int
    reactions: int
    outbox: int
    chats: int = 0
    contacts: int = 0


def _msg(row: sqlite3.Row) -> MessageRow:
    return MessageRow(
        id=row["id"],
        chat_jid=row["chat_jid"],
        wa_id=row["wa_id"],
        sender_jid=row["sender_jid"],
        from_me=bool(row["from_me"]),
        ts=row["ts"],
        kind=row["kind"],
        body=row["body"],
        quoted_wa_id=row["quoted_wa_id"],
        status=row["status"],
        error=row["error"],
        edited_at=row["edited_at"],
        deleted_at=row["deleted_at"],
    )


def _chat(row: sqlite3.Row) -> ChatRow:
    return ChatRow(
        jid=row["jid"],
        kind=row["kind"],
        title=row["title"],
        unread_count=row["unread_count"],
        last_message_at=row["last_message_at"],
        archived=bool(row["archived"]),
        muted_until=row["muted_until"],
        pinned=bool(row["pinned"]),
    )


def _outbox(row: sqlite3.Row) -> OutboxItem:
    return OutboxItem(
        request_id=row["request_id"],
        chat_jid=row["chat_jid"],
        payload=json.loads(row["payload"]),
        state=row["state"],
        attempts=row["attempts"],
        error=row["error"],
        created_at=row["created_at"],
        wa_id=row["wa_id"],
    )


def infer_kind(jid: str) -> ChatKind:
    if jid.endswith("@g.us"):
        return "group"
    if jid.endswith("@broadcast") or jid.endswith("@newsletter"):
        return "broadcast"
    return "direct"


def _clip(text: str | None) -> str | None:
    """Redacta JIDs y números largos y trunca a 200 caracteres (los errores no filtran PII)."""
    if text is None:
        return None
    return _DIGITS_RE.sub("***", _JID_RE.sub("***", text))[:_ERROR_MAX]


def _limit(n: int) -> int:
    return max(1, min(n, MAX_LIMIT))


def _body(text: str | None) -> str | None:
    return None if text is None else text[:MAX_BODY]


# --- chats y contactos -------------------------------------------------------------------


def upsert_chat(
    conn: sqlite3.Connection,
    jid: str,
    *,
    kind: ChatKind | None = None,
    title: str | None = None,
    archived: bool | None = None,
    muted_until: int | None = None,
    pinned: bool | None = None,
) -> None:
    """Crea o actualiza un chat; los argumentos None no pisan valores existentes."""
    params = {
        "jid": jid,
        "kind": kind or infer_kind(jid),
        "given_kind": kind,
        "title": title,
        "archived": None if archived is None else int(archived),
        "muted_until": muted_until,
        "pinned": None if pinned is None else int(pinned),
    }
    conn.execute(
        "INSERT INTO chats (jid, kind, title, archived, muted_until, pinned) "
        "VALUES (:jid, :kind, :title, COALESCE(:archived, 0), COALESCE(:muted_until, 0), "
        "COALESCE(:pinned, 0)) "
        "ON CONFLICT (jid) DO UPDATE SET "
        "kind = COALESCE(:given_kind, kind), "
        "title = COALESCE(:title, title), "
        "archived = COALESCE(:archived, archived), "
        "muted_until = COALESCE(:muted_until, muted_until), "
        "pinned = COALESCE(:pinned, pinned)",
        params,
    )


def upsert_contact(
    conn: sqlite3.Connection,
    jid: str,
    *,
    push_name: str | None = None,
    alias: str | None = None,
    is_business: bool | None = None,
    updated_at: int | None = None,
) -> None:
    params = {
        "jid": jid,
        "push_name": push_name,
        "alias": alias,
        "is_business": None if is_business is None else int(is_business),
        "updated_at": int(time.time()) if updated_at is None else updated_at,
    }
    conn.execute(
        "INSERT INTO contacts (jid, push_name, alias, is_business, updated_at) "
        "VALUES (:jid, :push_name, :alias, COALESCE(:is_business, 0), :updated_at) "
        "ON CONFLICT (jid) DO UPDATE SET "
        "push_name = COALESCE(:push_name, push_name), "
        "alias = COALESCE(:alias, alias), "
        "is_business = COALESCE(:is_business, is_business), "
        "updated_at = :updated_at",
        params,
    )


def list_chats(conn: sqlite3.Connection, limit: int = 50) -> list[ChatRow]:
    rows = conn.execute(
        "SELECT jid, kind, title, unread_count, last_message_at, archived, muted_until, pinned "
        "FROM chats WHERE archived = 0 ORDER BY last_message_at DESC LIMIT ?",
        (_limit(limit),),
    ).fetchall()
    return [_chat(r) for r in rows]


def mark_read(conn: sqlite3.Connection, chat_jid: str) -> bool:
    """Pone unread_count a 0. True si el chat existe."""
    cur = conn.execute("UPDATE chats SET unread_count = 0 WHERE jid = ?", (chat_jid,))
    return cur.rowcount > 0


# --- mensajes ---------------------------------------------------------------------------

_UPSERT_MESSAGE = (
    "INSERT INTO messages (chat_jid, wa_id, sender_jid, from_me, ts, kind, body, quoted_wa_id, "
    "status, error) "
    "VALUES (:chat_jid, :wa_id, :sender_jid, :from_me, :ts, COALESCE(:kind, 'text'), :body, "
    ":quoted_wa_id, COALESCE(:status, 'sent'), :error) "
    "ON CONFLICT (chat_jid, wa_id, COALESCE(sender_jid, '')) DO UPDATE SET "
    "sender_jid = COALESCE(:sender_jid, sender_jid), "
    "kind = COALESCE(:kind, kind), "
    "body = CASE WHEN deleted_at IS NOT NULL THEN body ELSE COALESCE(:body, body) END, "
    "quoted_wa_id = CASE WHEN deleted_at IS NOT NULL THEN quoted_wa_id "
    "ELSE COALESCE(:quoted_wa_id, quoted_wa_id) END, "
    "error = COALESCE(:error, error), "
    "status = CASE "
    "WHEN :status IS NULL THEN status "
    "WHEN :status = 'failed' THEN CASE WHEN status IN ('pending', 'sent') "
    "THEN 'failed' ELSE status END "
    "WHEN instr(' failed pending sent delivered read ', ' ' || :status || ' ') "
    "> instr(' failed pending sent delivered read ', ' ' || status || ' ') THEN :status "
    "ELSE status END "
    "WHERE messages.from_me = excluded.from_me "
    "AND (excluded.sender_jid IS NULL OR messages.sender_jid IS excluded.sender_jid) "
    "RETURNING id"
)


def upsert_message(
    conn: sqlite3.Connection, msg: MessageIn, *, count_unread: bool = True
) -> UpsertResult:
    """Inserta o fusiona un mensaje (idempotente por chat_jid+wa_id+autor).

    La unicidad es (chat_jid, wa_id, COALESCE(sender_jid, '')): el mismo wa_id con otro autor es
    otra fila. sender_jid=None en la entrada se fusiona con la única fila existente de ese
    (chat, wa_id); si hay varias es ambiguo y devuelve applied=False. No degrada el estado ni
    pisa campos con NULL. Si el existente difiere en from_me no toca nada (applied=False).
    Nunca modifica body/quoted de un mensaje borrado. Crea el chat si falta y actualiza
    last_message_at; suma unread_count solo en inserciones entrantes si count_unread.
    """
    sender = msg.sender_jid
    if sender is None:
        known = conn.execute(
            "SELECT id, sender_jid FROM messages WHERE chat_jid = ? AND wa_id = ? LIMIT 2",
            (msg.chat_jid, msg.wa_id),
        ).fetchall()
        if len(known) > 1:
            return UpsertResult(id=int(known[0]["id"]), inserted=False, applied=False)
        if known:
            sender = known[0]["sender_jid"]
    params = {
        "chat_jid": msg.chat_jid,
        "wa_id": msg.wa_id,
        "sender_jid": sender,
        "from_me": int(msg.from_me),
        "ts": msg.ts,
        "kind": msg.kind,
        "body": _body(msg.body),
        "quoted_wa_id": msg.quoted_wa_id,
        "status": msg.status,
        "error": _clip(msg.error),
    }
    with transaction(conn):
        existed = conn.execute(
            "SELECT 1 FROM messages "
            "WHERE chat_jid = ? AND wa_id = ? AND COALESCE(sender_jid, '') = COALESCE(?, '')",
            (msg.chat_jid, msg.wa_id, sender),
        ).fetchone()
        conn.execute(
            "INSERT INTO chats (jid, kind) VALUES (?, ?) ON CONFLICT (jid) DO NOTHING",
            (msg.chat_jid, infer_kind(msg.chat_jid)),
        )
        row = conn.execute(_UPSERT_MESSAGE, params).fetchone()
        if row is None:
            prior = conn.execute(
                "SELECT id FROM messages "
                "WHERE chat_jid = ? AND wa_id = ? AND COALESCE(sender_jid, '') = COALESCE(?, '')",
                (msg.chat_jid, msg.wa_id, sender),
            ).fetchone()
            return UpsertResult(id=int(prior["id"]), inserted=False, applied=False)
        inserted = existed is None
        bump = 1 if (inserted and not msg.from_me and count_unread) else 0
        conn.execute(
            "UPDATE chats SET last_message_at = MAX(COALESCE(last_message_at, 0), ?), "
            "unread_count = unread_count + ? WHERE jid = ?",
            (msg.ts, bump, msg.chat_jid),
        )
    return UpsertResult(id=int(row["id"]), inserted=inserted)


def update_status(
    conn: sqlite3.Connection,
    chat_jid: str,
    wa_id: str,
    status: Status,
    error: str | None = None,
) -> bool:
    """Avanza pending<sent<delivered<read; failed solo desde pending/sent. True si cambió."""
    cur = conn.execute(
        "UPDATE messages SET "
        "status = :status, "
        "error = CASE WHEN :status = 'failed' THEN :error ELSE NULL END "
        "WHERE chat_jid = :chat_jid AND wa_id = :wa_id AND CASE "
        "WHEN :status = 'failed' THEN status IN ('pending', 'sent') "
        "ELSE instr(' failed pending sent delivered read ', ' ' || :status || ' ') "
        "> instr(' failed pending sent delivered read ', ' ' || status || ' ') END",
        {"status": status, "error": _clip(error), "chat_jid": chat_jid, "wa_id": wa_id},
    )
    return cur.rowcount > 0


_AUTHOR = "AND from_me = :from_me AND (:from_me = 1 OR sender_jid = :sender_jid) "


def apply_edit(
    conn: sqlite3.Connection,
    chat_jid: str,
    wa_id: str,
    body: str,
    edited_at: int,
    *,
    sender_jid: str | None,
    from_me: bool = False,
) -> bool:
    """Edita solo si el autor coincide (from_me, o sender_jid exacto si es entrante).

    Entrantes con sender_jid NULL (almacenado o dado) nunca coinciden. No edita borrados.
    """
    cur = conn.execute(
        "UPDATE messages SET body = :body, edited_at = :edited_at "  # noqa: S608
        "WHERE chat_jid = :chat_jid AND wa_id = :wa_id AND deleted_at IS NULL " + _AUTHOR,
        {
            "body": _body(body),
            "edited_at": edited_at,
            "chat_jid": chat_jid,
            "wa_id": wa_id,
            "from_me": int(from_me),
            "sender_jid": sender_jid,
        },
    )
    return cur.rowcount > 0


def apply_delete(
    conn: sqlite3.Connection,
    chat_jid: str,
    wa_id: str,
    deleted_at: int,
    *,
    sender_jid: str | None,
    from_me: bool = False,
) -> bool:
    """Borrado lógico solo si el autor coincide: deleted_at, body=NULL, quoted_wa_id=NULL.

    El trigger AU limpia el índice FTS. Conserva la primera marca si ya estaba borrado.
    También elimina las reacciones del mensaje.
    """
    with transaction(conn):
        return _apply_delete(conn, chat_jid, wa_id, deleted_at, sender_jid, from_me)


def _apply_delete(
    conn: sqlite3.Connection,
    chat_jid: str,
    wa_id: str,
    deleted_at: int,
    sender_jid: str | None,
    from_me: bool,
) -> bool:
    cur = conn.execute(
        "UPDATE messages SET deleted_at = :deleted_at, body = NULL, quoted_wa_id = NULL "  # noqa: S608
        "WHERE chat_jid = :chat_jid AND wa_id = :wa_id AND deleted_at IS NULL " + _AUTHOR,
        {
            "deleted_at": deleted_at,
            "chat_jid": chat_jid,
            "wa_id": wa_id,
            "from_me": int(from_me),
            "sender_jid": sender_jid,
        },
    )
    if cur.rowcount > 0:
        conn.execute("DELETE FROM reactions WHERE chat_jid = ? AND wa_id = ?", (chat_jid, wa_id))
    return cur.rowcount > 0


def upsert_reaction(
    conn: sqlite3.Connection, chat_jid: str, wa_id: str, sender_jid: str, emoji: str, ts: int
) -> None:
    """Emoji vacío elimina la reacción; una reacción más antigua no pisa a la vigente."""
    if emoji == "":
        conn.execute(
            "DELETE FROM reactions WHERE chat_jid = ? AND wa_id = ? AND sender_jid = ?",
            (chat_jid, wa_id, sender_jid),
        )
        return
    conn.execute(
        "INSERT INTO reactions (chat_jid, wa_id, sender_jid, emoji, ts) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (chat_jid, wa_id, sender_jid) DO UPDATE SET "
        "emoji = excluded.emoji, ts = excluded.ts WHERE excluded.ts >= reactions.ts",
        (chat_jid, wa_id, sender_jid, emoji, ts),
    )


def messages_since(conn: sqlite3.Connection, ts: int, limit: int = 1000) -> list[MessageRow]:
    """Mensajes con ts posterior al indicado, en orden cronológico."""
    rows = conn.execute(
        f"SELECT {_MSG_COLS} FROM messages m WHERE m.ts > ? ORDER BY m.ts, m.id LIMIT ?",  # noqa: S608
        (ts, _limit(limit)),
    ).fetchall()
    return [_msg(r) for r in rows]


def last_messages(conn: sqlite3.Connection, chat_jid: str, n: int = 50) -> list[MessageRow]:
    """Últimos n mensajes del chat, devueltos en orden cronológico ascendente."""
    rows = conn.execute(
        f"SELECT {_MSG_COLS} FROM messages m WHERE m.chat_jid = ? "  # noqa: S608
        "ORDER BY m.ts DESC, m.id DESC LIMIT ?",
        (chat_jid, _limit(n)),
    ).fetchall()
    return [_msg(r) for r in reversed(rows)]


def fts_query(text: str) -> str:
    """Texto libre a consulta FTS5 segura: máx. 16 tokens citados de ≤64 chars, AND.

    El último token es prefijo; uno recortado por longitud también (para seguir coincidiendo).
    """
    tokens = _TOKEN_RE.findall(text)[:MAX_FTS_TOKENS]
    if not tokens:
        return ""
    parts = [
        '"' + t[:MAX_FTS_TOKEN_LEN] + '"' + ("*" if len(t) > MAX_FTS_TOKEN_LEN else "")
        for t in tokens
    ]
    if not parts[-1].endswith("*"):
        parts[-1] += "*"
    return " ".join(parts)


def search(conn: sqlite3.Connection, q: str, limit: int = 50) -> list[MessageRow]:
    """Búsqueda de texto completo, por relevancia (bm25) y luego más reciente."""
    match = fts_query(q)
    if not match:
        return []
    rows = conn.execute(
        f"SELECT {_MSG_COLS} "  # noqa: S608
        "FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid "
        "WHERE messages_fts MATCH ? AND m.deleted_at IS NULL "
        "ORDER BY messages_fts.rank, m.ts DESC LIMIT ?",
        (match, _limit(limit)),
    ).fetchall()
    return [_msg(r) for r in rows]


# --- outbox -----------------------------------------------------------------------------


def outbox_put(
    conn: sqlite3.Connection,
    request_id: str,
    chat_jid: str,
    payload: Mapping[str, Any],
    created_at: int | None = None,
) -> bool:
    """Encola un envío; idempotente por request_id. True si se insertó.

    Lanza ValueError si el payload serializado supera MAX_PAYLOAD_BYTES.
    """
    text = json.dumps(payload, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload de outbox demasiado grande")
    cur = conn.execute(
        "INSERT INTO outbox (request_id, chat_jid, payload, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (request_id) DO NOTHING",
        (
            request_id,
            chat_jid,
            text,
            int(time.time()) if created_at is None else created_at,
        ),
    )
    return cur.rowcount > 0


def outbox_next(conn: sqlite3.Connection) -> OutboxItem | None:
    """Toma el elemento queued más antiguo, lo pasa a sending y suma un intento."""
    with transaction(conn):
        row = conn.execute(
            "SELECT request_id FROM outbox WHERE state = 'queued' "
            "ORDER BY created_at, rowid LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        claimed = conn.execute(
            "UPDATE outbox SET state = 'sending', attempts = attempts + 1 "
            "WHERE request_id = ? RETURNING request_id, chat_jid, payload, state, attempts, "
            "error, created_at, wa_id",
            (row["request_id"],),
        ).fetchone()
    return _outbox(claimed)


def outbox_get(conn: sqlite3.Connection, request_id: str) -> OutboxItem | None:
    """Devuelve el elemento por request_id (cualquier estado) o None."""
    row = conn.execute(
        "SELECT request_id, chat_jid, payload, state, attempts, error, created_at, wa_id "
        "FROM outbox WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    return None if row is None else _outbox(row)


def outbox_done(conn: sqlite3.Connection, request_id: str, wa_id: str | None = None) -> bool:
    """Marca done, guarda el wa_id real y vacía el payload; la fila queda para idempotencia."""
    cur = conn.execute(
        "UPDATE outbox SET state = 'done', error = NULL, payload = '{}', "
        "wa_id = COALESCE(?, wa_id) WHERE request_id = ?",
        (wa_id, request_id),
    )
    return cur.rowcount > 0


def outbox_recover(conn: sqlite3.Connection) -> int:
    """Al arrancar: 'sending' pasa a 'failed' (error 'interrupted'); no se reenvía.

    El envío pudo haber salido antes de la caída. Vacía el payload. Devuelve cuántas filas.
    """
    cur = conn.execute(
        "UPDATE outbox SET state = 'failed', error = 'interrupted', payload = '{}' "
        "WHERE state = 'sending'"
    )
    return cur.rowcount


def outbox_fail(
    conn: sqlite3.Connection, request_id: str, error: str, *, retry: bool = False
) -> bool:
    """Registra el error; retry=True devuelve el elemento a queued.

    Si no, queda failed (definitivo) y se vacía el payload ('{}').
    """
    cur = conn.execute(
        "UPDATE outbox SET state = CASE WHEN ? THEN 'queued' ELSE 'failed' END, error = ?, "
        "payload = CASE WHEN ? THEN payload ELSE '{}' END WHERE request_id = ?",
        (int(retry), _clip(error), int(retry), request_id),
    )
    return cur.rowcount > 0


# --- reconciliación de ids y FTS ---------------------------------------------------------

_RANK = "' failed pending sent delivered read '"


def rebind_wa_id(conn: sqlite3.Connection, chat_jid: str, old_wa_id: str, new_wa_id: str) -> bool:
    """Renombra el mensaje placeholder (propio) al wa_id real asignado por WhatsApp.

    Si ya existe una fila propia con new_wa_id (el eco llegó antes), borra el placeholder y
    conserva la existente con el estado más avanzado de ambas. Idempotente: False si no hay
    placeholder (ya reasignado) o si old == new.
    """
    if old_wa_id == new_wa_id:
        return False
    with transaction(conn):
        old = conn.execute(
            "SELECT id, status FROM messages WHERE chat_jid = ? AND wa_id = ? AND from_me = 1",
            (chat_jid, old_wa_id),
        ).fetchone()
        if old is None:
            return False
        keep = conn.execute(
            "SELECT id FROM messages WHERE chat_jid = ? AND wa_id = ? AND from_me = 1",
            (chat_jid, new_wa_id),
        ).fetchone()
        if keep is None:
            cur = conn.execute(
                "UPDATE OR IGNORE messages SET wa_id = ? WHERE id = ?", (new_wa_id, old["id"])
            )
            return cur.rowcount > 0
        conn.execute(
            "UPDATE messages SET status = ? WHERE id = ? AND "  # noqa: S608
            f"instr({_RANK}, ' ' || ? || ' ') > instr({_RANK}, ' ' || status || ' ')",
            (old["status"], keep["id"], old["status"]),
        )
        conn.execute("DELETE FROM messages WHERE id = ?", (old["id"],))
    return True


def fts_optimize(conn: sqlite3.Connection) -> None:
    """Compacta el índice FTS5 (merge de segmentos)."""
    conn.execute("INSERT INTO messages_fts (messages_fts) VALUES ('optimize')")


# --- meta y retención -------------------------------------------------------------------


def meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def purge_older_than(conn: sqlite3.Connection, days: int, now: int | None = None) -> PurgeResult:
    """Borra mensajes anteriores a `days` y deja el almacén sin huérfanos.

    Reacciones sin mensaje; chats cuyo último mensaje es anterior al corte, sin mensajes, sin
    outbox pendiente y sin fijar ni silenciar; contactos sin alias, sin referencias y
    actualizados antes del corte. Outbox: done/failed anteriores al corte se borran; 'sending'
    con más de STUCK_SENDING_SECONDS se marca 'failed' (error 'stuck', payload vacío) en vez de
    borrarse. El FTS se mantiene por triggers; con secure_delete=ON (open_write) las páginas se
    sobrescriben. Tras purgar (fuera de transacción) hace wal_checkpoint(TRUNCATE) y FTS
    'optimize'. El VACUUM periódico queda a cargo del daemon (`db.vacuum`).
    """
    if days <= 0:
        raise ValueError("days debe ser positivo")
    current = int(time.time()) if now is None else now
    cutoff = current - days * _DAY
    with transaction(conn):
        msgs = conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff,)).rowcount
        reacts = conn.execute(
            "DELETE FROM reactions WHERE NOT EXISTS ("
            "SELECT 1 FROM messages m "
            "WHERE m.chat_jid = reactions.chat_jid AND m.wa_id = reactions.wa_id)"
        ).rowcount
        conn.execute(
            "UPDATE outbox SET state = 'failed', error = 'stuck', payload = '{}' "
            "WHERE state = 'sending' AND created_at < ?",
            (current - STUCK_SENDING_SECONDS,),
        )
        outbox = conn.execute(
            "DELETE FROM outbox WHERE state IN ('done', 'failed') AND created_at < ?",
            (cutoff,),
        ).rowcount
        chats = conn.execute(
            "DELETE FROM chats WHERE last_message_at < ? AND pinned = 0 AND muted_until <= ? "
            "AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.chat_jid = chats.jid) "
            "AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.chat_jid = chats.jid)",
            (cutoff, current),
        ).rowcount
        contacts = conn.execute(
            "DELETE FROM contacts WHERE alias IS NULL AND updated_at < ? "
            "AND NOT EXISTS (SELECT 1 FROM chats c WHERE c.jid = contacts.jid) "
            "AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.sender_jid = contacts.jid) "
            "AND NOT EXISTS (SELECT 1 FROM reactions r WHERE r.sender_jid = contacts.jid)",
            (cutoff,),
        ).rowcount
    if not conn.in_transaction:
        fts_optimize(conn)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    return PurgeResult(
        messages=msgs, reactions=reacts, outbox=outbox, chats=chats, contacts=contacts
    )
