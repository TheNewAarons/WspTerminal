import sqlite3

from wsp.store import repo
from wsp.store.repo import MessageIn

CHAT = "5215500000000@s.whatsapp.net"
DAY = 86_400


def _status(conn: sqlite3.Connection, wa_id: str = "1") -> str:
    return str(
        conn.execute(
            "SELECT status FROM messages WHERE chat_jid = ? AND wa_id = ?", (CHAT, wa_id)
        ).fetchone()[0]
    )


def _count(conn: sqlite3.Connection, table: str) -> int:
    assert table in {"messages", "reactions", "outbox"}
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


# --- idempotencia -----------------------------------------------------------------------


def test_upsert_message_idempotent(conn: sqlite3.Connection) -> None:
    a = repo.upsert_message(conn, MessageIn(CHAT, "1", 100, body="hola", sender_jid="s"))
    b = repo.upsert_message(conn, MessageIn(CHAT, "1", 100, body="hola", sender_jid="s"))
    assert a.inserted is True
    assert b.inserted is False
    assert a.id == b.id
    assert _count(conn, "messages") == 1
    # el segundo upsert entrante no vuelve a sumar no leídos
    assert repo.list_chats(conn)[0].unread_count == 1


def test_upsert_does_not_clobber_with_null(conn: sqlite3.Connection) -> None:
    repo.upsert_message(
        conn,
        MessageIn(CHAT, "1", 100, body="hola", sender_jid="s", kind="image", quoted_wa_id="q"),
    )
    repo.upsert_message(conn, MessageIn(CHAT, "1", 100))
    m = repo.last_messages(conn, CHAT)[0]
    assert (m.body, m.sender_jid, m.kind, m.quoted_wa_id) == ("hola", "s", "image", "q")


def test_upsert_does_not_downgrade_status(conn: sqlite3.Connection) -> None:
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1, status="read"))
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1, status="sent"))
    assert _status(conn) == "read"
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1, status="failed"))
    assert _status(conn) == "read"
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1))
    assert _status(conn) == "read"


def test_upsert_advances_status(conn: sqlite3.Connection) -> None:
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1, status="pending"))
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1, status="delivered"))
    assert _status(conn) == "delivered"


def test_update_status_only_advances(conn: sqlite3.Connection) -> None:
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1, from_me=True, status="pending"))
    assert repo.update_status(conn, CHAT, "1", "sent") is True
    assert repo.update_status(conn, CHAT, "1", "pending") is False
    assert repo.update_status(conn, CHAT, "1", "sent") is False
    assert repo.update_status(conn, CHAT, "1", "read") is True
    assert repo.update_status(conn, CHAT, "1", "delivered") is False
    assert repo.update_status(conn, CHAT, "1", "failed", "x") is False
    assert _status(conn) == "read"
    assert repo.update_status(conn, CHAT, "nope", "read") is False


def test_update_status_failed_stores_error(conn: sqlite3.Connection) -> None:
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1, from_me=True, status="pending"))
    assert repo.update_status(conn, CHAT, "1", "failed", "e" * 1000) is True
    row = conn.execute("SELECT status, error FROM messages").fetchone()
    assert row["status"] == "failed"
    assert row["error"] == "e" * 500


def test_unread_only_for_incoming(conn: sqlite3.Connection) -> None:
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1))
    repo.upsert_message(conn, MessageIn(CHAT, "2", 2, from_me=True))
    repo.upsert_message(conn, MessageIn(CHAT, "3", 3), count_unread=False)
    assert repo.list_chats(conn)[0].unread_count == 1


# --- FTS --------------------------------------------------------------------------------


def _hits(conn: sqlite3.Connection, q: str) -> list[str]:
    return [m.wa_id for m in repo.search(conn, q)]


def test_fts_accent_insensitive(conn: sqlite3.Connection) -> None:
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1, body="qué canción tan bonita"))
    repo.upsert_message(conn, MessageIn(CHAT, "2", 2, body="otra cancion sin tilde"))
    assert sorted(_hits(conn, "cancion")) == ["1", "2"]
    assert sorted(_hits(conn, "canción")) == ["1", "2"]
    assert _hits(conn, "canc") != []  # prefijo


def test_fts_after_edit(conn: sqlite3.Connection) -> None:
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1, body="manzana"))
    assert repo.apply_edit(conn, CHAT, "1", "pera", 5) is True
    assert _hits(conn, "manzana") == []
    assert _hits(conn, "pera") == ["1"]
    m = repo.last_messages(conn, CHAT)[0]
    assert m.edited_at == 5


def test_fts_after_delete(conn: sqlite3.Connection) -> None:
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1, body="secreto"))
    assert repo.apply_delete(conn, CHAT, "1", 9) is True
    assert repo.apply_delete(conn, CHAT, "1", 10) is False
    assert _hits(conn, "secreto") == []
    assert repo.apply_edit(conn, CHAT, "1", "nuevo", 11) is False


def test_fts_after_purge(conn: sqlite3.Connection) -> None:
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1, body="viejo"))
    repo.upsert_message(conn, MessageIn(CHAT, "2", 10 * DAY, body="reciente"))
    repo.purge_older_than(conn, 1, now=10 * DAY)
    assert _hits(conn, "viejo") == []
    assert _hits(conn, "reciente") == ["2"]
    row = conn.execute("INSERT INTO messages_fts (messages_fts) VALUES ('integrity-check')")
    assert row is not None  # no lanza: índice coherente con la tabla


def test_fts_syntax_chars_do_not_raise(conn: sqlite3.Connection) -> None:
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1, body="foo bar OR baz"))
    for q in ['"', '"foo', "foo*", "OR", "-foo", "foo OR", "a AND", "(", "NEAR(", "col:x", "'; --"]:
        repo.search(conn, q)
    assert repo.search(conn, "") == []
    assert repo.search(conn, '""*-') == []
    assert _hits(conn, '"foo" -bar') == ["1"]


# --- retención --------------------------------------------------------------------------


def test_purge_older_than(conn: sqlite3.Connection) -> None:
    now = 100 * DAY
    repo.upsert_message(conn, MessageIn(CHAT, "old", now - 50 * DAY, body="a"))
    repo.upsert_message(conn, MessageIn(CHAT, "new", now - 1 * DAY, body="b"))
    repo.upsert_reaction(conn, CHAT, "old", "s", "x", 1)
    repo.upsert_reaction(conn, CHAT, "new", "s", "y", 1)
    repo.outbox_put(conn, "r-old", CHAT, {"t": 1}, created_at=now - 50 * DAY)
    repo.outbox_put(conn, "r-new", CHAT, {"t": 1}, created_at=now - 1 * DAY)
    repo.outbox_put(conn, "r-old-q", CHAT, {"t": 1}, created_at=now - 50 * DAY)
    repo.outbox_put(conn, "r-new-d", CHAT, {"t": 1}, created_at=now - 1 * DAY)
    repo.outbox_next(conn)  # r-old -> sending
    repo.outbox_done(conn, "r-old")
    repo.outbox_fail(conn, "r-new", "x")  # failed pero reciente

    res = repo.purge_older_than(conn, 30, now=now)
    assert (res.messages, res.reactions, res.outbox) == (1, 1, 1)
    assert [m.wa_id for m in repo.last_messages(conn, CHAT)] == ["new"]
    assert _count(conn, "reactions") == 1
    left = {r[0] for r in conn.execute("SELECT request_id FROM outbox")}
    assert left == {"r-new", "r-old-q", "r-new-d"}  # queued antiguo no se borra
    assert _hits(conn, "a") == []
    assert _hits(conn, "b") == ["new"]


def test_purge_rejects_nonpositive_days(conn: sqlite3.Connection) -> None:
    import pytest

    with pytest.raises(ValueError):
        repo.purge_older_than(conn, 0)


# --- outbox -----------------------------------------------------------------------------


def test_outbox_cycle_done(conn: sqlite3.Connection) -> None:
    assert repo.outbox_next(conn) is None
    assert repo.outbox_put(conn, "r1", CHAT, {"text": "canción"}, created_at=1) is True
    assert repo.outbox_put(conn, "r1", CHAT, {"text": "otro"}, created_at=2) is False
    assert _count(conn, "outbox") == 1
    item = repo.outbox_next(conn)
    assert item is not None
    assert (item.request_id, item.state, item.attempts) == ("r1", "sending", 1)
    assert item.payload == {"text": "canción"}
    assert repo.outbox_next(conn) is None
    assert repo.outbox_done(conn, "r1") is True
    row = conn.execute("SELECT state, error FROM outbox").fetchone()
    assert (row["state"], row["error"]) == ("done", None)
    assert repo.outbox_done(conn, "zzz") is False


def test_outbox_fail_and_retry(conn: sqlite3.Connection) -> None:
    repo.outbox_put(conn, "r1", CHAT, {"a": 1}, created_at=1)
    repo.outbox_put(conn, "r2", CHAT, {"a": 2}, created_at=2)
    first = repo.outbox_next(conn)
    assert first is not None
    assert first.request_id == "r1"  # FIFO
    assert repo.outbox_fail(conn, "r1", "net", retry=True) is True
    again = repo.outbox_next(conn)
    assert again is not None
    assert (again.request_id, again.attempts, again.error) == ("r1", 2, "net")
    assert repo.outbox_fail(conn, "r1", "x" * 900) is True
    row = conn.execute("SELECT state, attempts, error FROM outbox WHERE request_id='r1'").fetchone()
    assert (row["state"], row["attempts"], len(row["error"])) == ("failed", 2, 500)
    nxt = repo.outbox_next(conn)
    assert nxt is not None
    assert nxt.request_id == "r2"
    assert repo.outbox_fail(conn, "nope", "e") is False


# --- extras -----------------------------------------------------------------------------


def test_last_messages_order_and_limit(conn: sqlite3.Connection) -> None:
    for i in range(10):
        repo.upsert_message(conn, MessageIn(CHAT, str(i), 100 + i, body=str(i)))
    repo.upsert_message(conn, MessageIn("other@s.whatsapp.net", "x", 500))
    got = repo.last_messages(conn, CHAT, 3)
    assert [m.wa_id for m in got] == ["7", "8", "9"]
    assert len(repo.last_messages(conn, CHAT)) == 10
    assert repo.last_messages(conn, "none") == []


def test_messages_since(conn: sqlite3.Connection) -> None:
    for i in range(5):
        repo.upsert_message(conn, MessageIn(CHAT, str(i), 10 * i))
    assert [m.wa_id for m in repo.messages_since(conn, 20)] == ["3", "4"]
    assert [m.wa_id for m in repo.messages_since(conn, -1, limit=2)] == ["0", "1"]
    assert repo.messages_since(conn, 1000) == []


def test_list_chats_ordered_and_hides_archived(conn: sqlite3.Connection) -> None:
    for i, jid in enumerate(["a@s.whatsapp.net", "b@g.us", "c@s.whatsapp.net"]):
        repo.upsert_message(conn, MessageIn(jid, "1", 10 * (i + 1)))
    repo.upsert_chat(conn, "c@s.whatsapp.net", archived=True)
    chats = repo.list_chats(conn)
    assert [c.jid for c in chats] == ["b@g.us", "a@s.whatsapp.net"]
    assert chats[0].kind == "group"
    assert len(repo.list_chats(conn, limit=1)) == 1


def test_upsert_chat_none_keeps_values(conn: sqlite3.Connection) -> None:
    repo.upsert_chat(conn, CHAT, title="Ana", pinned=True)
    repo.upsert_chat(conn, CHAT)
    c = repo.list_chats(conn)[0]
    assert (c.title, c.pinned) == ("Ana", True)


def test_mark_read(conn: sqlite3.Connection) -> None:
    repo.upsert_message(conn, MessageIn(CHAT, "1", 1))
    repo.upsert_message(conn, MessageIn(CHAT, "2", 2))
    assert repo.list_chats(conn)[0].unread_count == 2
    assert repo.mark_read(conn, CHAT) is True
    assert repo.list_chats(conn)[0].unread_count == 0
    assert repo.mark_read(conn, "nope") is False


def test_reactions(conn: sqlite3.Connection) -> None:
    repo.upsert_reaction(conn, CHAT, "1", "s", "👍", 10)
    repo.upsert_reaction(conn, CHAT, "1", "s", "❤", 5)  # más antigua: no pisa
    assert conn.execute("SELECT emoji FROM reactions").fetchone()[0] == "👍"
    repo.upsert_reaction(conn, CHAT, "1", "s", "❤", 11)
    assert conn.execute("SELECT emoji FROM reactions").fetchone()[0] == "❤"
    repo.upsert_reaction(conn, CHAT, "1", "s", "", 12)
    assert _count(conn, "reactions") == 0


def test_meta_and_contact(conn: sqlite3.Connection) -> None:
    assert repo.meta_get(conn, "k") is None
    repo.meta_set(conn, "k", "1")
    repo.meta_set(conn, "k", "2")
    assert repo.meta_get(conn, "k") == "2"
    repo.upsert_contact(conn, CHAT, push_name="Ana", updated_at=1)
    repo.upsert_contact(conn, CHAT, alias="A", updated_at=2)
    row = conn.execute("SELECT push_name, alias FROM contacts").fetchone()
    assert (row["push_name"], row["alias"]) == ("Ana", "A")


def test_infer_kind() -> None:
    assert repo.infer_kind("x@g.us") == "group"
    assert repo.infer_kind("x@broadcast") == "broadcast"
    assert repo.infer_kind("x@newsletter") == "broadcast"
    assert repo.infer_kind("x@s.whatsapp.net") == "direct"


def test_query_plans_use_indexes(conn: sqlite3.Connection) -> None:
    rows = [
        (f"{i % 50}@s.whatsapp.net", str(i), 1_000 + i, f"hola mundo {i}") for i in range(5_000)
    ]
    with conn:
        conn.execute("BEGIN")
        for jid in {r[0] for r in rows}:
            repo.upsert_chat(conn, jid)
        conn.executemany(
            "INSERT INTO messages (chat_jid, wa_id, ts, body) VALUES (?, ?, ?, ?)", rows
        )
        conn.execute("UPDATE chats SET last_message_at = 1000 + rowid")
        conn.execute("COMMIT")
    conn.execute("ANALYZE")

    plans = {
        "last": (
            "SELECT * FROM messages WHERE chat_jid = ? ORDER BY ts DESC, id DESC LIMIT ?",
            ("3@s.whatsapp.net", 50),
        ),
        "since": ("SELECT * FROM messages WHERE ts > ? ORDER BY ts, id LIMIT ?", (5_000, 100)),
        "chats": (
            "SELECT * FROM chats WHERE archived = 0 ORDER BY last_message_at DESC LIMIT ?",
            (50,),
        ),
    }
    for name, (sql, params) in plans.items():
        details = [r["detail"] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]
        text = " | ".join(details)
        assert "USING" in text and "INDEX" in text, (name, text)
        assert "SCAN messages" not in text.replace("SCAN messages USING", ""), (name, text)
        assert "SCAN chats" not in text.replace("SCAN chats USING", ""), (name, text)
        assert "TEMP B-TREE" not in text, (name, text)
