"""Outbox (idempotencia, reintentos, token bucket) y Commands."""

import sqlite3

import pytest

from tests.daemon.fakes import DIRECT_STR, FakeClient, FakeClock
from wsp.daemon.config import OutboxConfig
from wsp.daemon.outbox import Commands, Outbox, TokenBucket, placeholder_id
from wsp.protocol.models import (
    Event,
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
from wsp.store import repo
from wsp.store.writer import Writer

SECRET = "token-supersecreto-123"  # noqa: S105


class Rig:
    def __init__(self, conn: sqlite3.Connection, writer: Writer, cfg: OutboxConfig) -> None:
        self.conn = conn
        self.client = FakeClient()
        self.events: list[Event] = []
        self.sleeps: list[float] = []
        self.clock = FakeClock()
        self.outbox = Outbox(
            writer,
            self.client,
            cfg,
            self.events.append,
            clock=self.clock,
            sleep=self.sleep,
            now=lambda: 1_700_000_000,
        )
        self.commands = Commands(writer, self.outbox)

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.clock.now += delay

    async def drain(self) -> int:
        n = 0
        while (item := repo.outbox_next(self.conn)) is not None:
            await self.outbox.process(item)
            n += 1
        return n

    def message(self, wa_id: str) -> repo.MessageRow | None:
        rows = repo.last_messages(self.conn, DIRECT_STR)
        return next((r for r in rows if r.wa_id == wa_id), None)


@pytest.fixture
def rig(conn: sqlite3.Connection, writer: Writer) -> Rig:
    return Rig(
        conn, writer, OutboxConfig(rate_per_sec=10, burst=10, max_attempts=3, retry_base=1.0)
    )


# ---- TokenBucket --------------------------------------------------------------------------


def test_bucket_burst_then_wait_then_refill() -> None:
    clock = FakeClock(0.0)
    b = TokenBucket(rate=2.0, burst=2, clock=clock)
    assert b.take() == 0.0
    assert b.take() == 0.0
    assert b.take() == pytest.approx(0.5)
    clock.now += 0.25
    assert b.take() == pytest.approx(0.25)
    clock.now += 0.25
    assert b.take() == 0.0


def test_bucket_refill_is_capped_at_burst() -> None:
    clock = FakeClock(0.0)
    b = TokenBucket(rate=1.0, burst=2, clock=clock)
    clock.now += 1000
    assert [b.take(), b.take()] == [0.0, 0.0]
    assert b.take() > 0


async def test_bucket_acquire_waits() -> None:
    clock = FakeClock(0.0)
    b = TokenBucket(rate=1.0, burst=1, clock=clock)
    waits: list[float] = []

    async def sleep(d: float) -> None:
        waits.append(d)
        clock.now += d

    await b.acquire(sleep)
    assert waits == []
    await b.acquire(sleep)
    assert waits == [pytest.approx(1.0)]


# ---- enqueue / envío ----------------------------------------------------------------------


async def test_enqueue_creates_pending_placeholder_and_emits(rig: Rig) -> None:
    res = await rig.outbox.enqueue("r1", {"kind": "text", "text": "hola"}, DIRECT_STR)
    assert res == {"state": "queued", "wa_id": "wsp:r1"}
    row = rig.message("wsp:r1")
    assert row is not None and (row.status, row.from_me, row.body) == ("pending", True, "hola")
    assert isinstance(rig.events[0], MessageNew) and rig.events[0].status == "pending"
    assert repo.list_chats(rig.conn)[0].unread_count == 0


async def test_send_rebinds_to_real_id_and_marks_sent(rig: Rig) -> None:
    await rig.outbox.enqueue("r1", {"kind": "text", "text": "hola"}, DIRECT_STR)
    assert await rig.drain() == 1
    assert rig.message("wsp:r1") is None
    row = rig.message("WA1")
    assert row is not None and row.status == "sent"
    assert rig.events[-1] == MessageStatus(chat_jid=DIRECT_STR, wa_id="WA1", status="sent")
    assert rig.client.sent == [(DIRECT_STR, "hola", None, None)]
    item = repo.outbox_get(rig.conn, "r1")
    assert item is not None and (item.state, item.wa_id, item.payload) == ("done", "WA1", {})


async def test_request_id_is_idempotent(rig: Rig) -> None:
    payload = {"kind": "text", "text": "hola"}
    await rig.outbox.enqueue("r1", payload, DIRECT_STR)
    await rig.drain()
    n_events = len(rig.events)
    again = await rig.outbox.enqueue("r1", payload, DIRECT_STR)
    assert again == {"state": "done", "wa_id": "WA1"}
    assert await rig.drain() == 0
    assert len(rig.client.sent) == 1
    assert len(rig.events) == n_events
    assert len(repo.last_messages(rig.conn, DIRECT_STR)) == 1


async def test_same_request_id_while_queued_does_not_duplicate(rig: Rig) -> None:
    await rig.outbox.enqueue("r1", {"kind": "text", "text": "a"}, DIRECT_STR)
    res = await rig.outbox.enqueue("r1", {"kind": "text", "text": "b"}, DIRECT_STR)
    assert res["state"] == "queued"
    await rig.drain()
    assert rig.client.sent == [(DIRECT_STR, "a", None, None)]


async def test_retries_with_backoff_then_succeeds(rig: Rig) -> None:
    rig.client.send_errors = [RuntimeError(SECRET), RuntimeError(SECRET)]
    await rig.outbox.enqueue("r1", {"kind": "text", "text": "hola"}, DIRECT_STR)
    assert await rig.drain() == 3
    assert rig.sleeps == [1.0, 2.0]
    row = rig.message("WA1")
    assert row is not None and row.status == "sent"


@pytest.mark.parametrize(
    ("exc", "code", "attempts"),
    [(RuntimeError(SECRET), "send_failed", 3), (TimeoutError(SECRET), "send_timeout", 1)],
)
async def test_fails_with_stable_code(rig: Rig, exc: Exception, code: str, attempts: int) -> None:
    # Un timeout pudo enviar el mensaje: falla de inmediato, sin reintento (evita duplicados).
    rig.client.send_errors = [exc, exc, exc, exc]
    await rig.outbox.enqueue("r1", {"kind": "text", "text": "hola"}, DIRECT_STR)
    assert await rig.drain() == attempts
    assert len(rig.client.sent) == attempts
    assert rig.sleeps == [1.0, 2.0][: attempts - 1]
    row = rig.message("wsp:r1")
    assert row is not None and (row.status, row.error) == ("failed", code)
    last = rig.events[-1]
    assert isinstance(last, MessageStatus) and (last.status, last.error) == ("failed", code)
    item = repo.outbox_get(rig.conn, "r1")
    assert item is not None and (item.state, item.error) == ("failed", code)
    assert SECRET not in repr(rig.events)
    assert SECRET not in str(item)
    # reintentar el mismo request_id devuelve el fallo previo sin reenviar
    assert await rig.outbox.enqueue("r1", {"kind": "text", "text": "hola"}, DIRECT_STR) == {
        "state": "failed",
        "wa_id": "wsp:r1",
        "error": code,
    }
    assert await rig.drain() == 0


async def test_bucket_throttles_sends(conn: sqlite3.Connection, writer: Writer) -> None:
    rig = Rig(conn, writer, OutboxConfig(rate_per_sec=1, burst=1, max_attempts=1, retry_base=1.0))
    await rig.outbox.enqueue("r1", {"kind": "text", "text": "a"}, DIRECT_STR)
    await rig.outbox.enqueue("r2", {"kind": "text", "text": "b"}, DIRECT_STR)
    await rig.drain()
    assert rig.sleeps == [pytest.approx(1.0)]
    assert len(rig.client.sent) == 2


async def test_reply_sends_quote_info(rig: Rig) -> None:
    repo.upsert_message(
        rig.conn,
        repo.MessageIn(chat_jid=DIRECT_STR, wa_id="Q1", ts=1, sender_jid=DIRECT_STR, body="x"),
    )
    await rig.commands("r1", SendReply(chat_jid=DIRECT_STR, quoted_wa_id="Q1", text="resp"))
    await rig.drain()
    assert rig.client.sent == [(DIRECT_STR, "resp", "Q1", DIRECT_STR)]
    row = rig.message("WA1")
    assert row is not None and row.quoted_wa_id == "Q1"


async def test_react_sends_reaction_and_persists(rig: Rig) -> None:
    repo.upsert_message(
        rig.conn,
        repo.MessageIn(chat_jid=DIRECT_STR, wa_id="Q1", ts=1, sender_jid=DIRECT_STR, body="x"),
    )
    res = await rig.commands("r1", React(chat_jid=DIRECT_STR, wa_id="Q1", emoji="👍"))
    assert res["state"] == "queued"
    n = len(rig.events)
    await rig.drain()
    assert rig.client.reactions == [(DIRECT_STR, "Q1", DIRECT_STR, "👍")]
    assert rig.conn.execute("SELECT emoji, sender_jid FROM reactions").fetchone()["emoji"] == "👍"
    assert len(rig.events) == n  # las reacciones no emiten message.status


async def test_react_unknown_message_not_found(rig: Rig) -> None:
    with pytest.raises(CommandError) as exc:
        await rig.commands("r1", React(chat_jid=DIRECT_STR, wa_id="NOPE", emoji="👍"))
    assert exc.value.code == "not_found"
    assert repo.outbox_get(rig.conn, "r1") is None


async def test_react_failure_does_not_touch_messages(rig: Rig) -> None:
    repo.upsert_message(
        rig.conn,
        repo.MessageIn(chat_jid=DIRECT_STR, wa_id="Q1", ts=1, sender_jid=DIRECT_STR, body="x"),
    )
    rig.client.send_errors = [RuntimeError(SECRET)] * 3
    await rig.commands("r1", React(chat_jid=DIRECT_STR, wa_id="Q1", emoji="👍"))
    await rig.drain()
    item = repo.outbox_get(rig.conn, "r1")
    assert item is not None and (item.state, item.error) == ("failed", "send_failed")
    assert rig.events == []


async def test_stopping_prevents_retry(rig: Rig) -> None:
    rig.client.send_errors = [RuntimeError(SECRET)]
    await rig.outbox.enqueue("r1", {"kind": "text", "text": "x"}, DIRECT_STR)
    await rig.outbox.stop()
    await rig.drain()
    item = repo.outbox_get(rig.conn, "r1")
    assert item is not None and item.state == "failed"


async def test_worker_loop_sends_in_background(rig: Rig) -> None:
    rig.outbox.start()
    await rig.outbox.enqueue("r1", {"kind": "text", "text": "x"}, DIRECT_STR)
    await rig.outbox.stop(drain_seconds=2.0)
    # stop() marca _stopping: el worker puede o no haber tomado el elemento, pero no revienta
    item = repo.outbox_get(rig.conn, "r1")
    assert item is not None and item.state in {"queued", "sending", "done"}


async def test_placeholder_id() -> None:
    assert placeholder_id("abc") == "wsp:abc"


# ---- Commands -----------------------------------------------------------------------------


async def test_commands_send_text(rig: Rig) -> None:
    res = await rig.commands("r9", SendText(chat_jid=DIRECT_STR, text="hola"))
    assert res == {"state": "queued", "wa_id": "wsp:r9"}


async def test_commands_huge_payload_is_command_error(rig: Rig) -> None:
    big = "é" * 65_536  # válido para pydantic (65536 chars) pero >64 KiB en bytes
    with pytest.raises(CommandError) as e:
        await rig.commands("r1", SendText(chat_jid=DIRECT_STR, text=big))
    assert e.value.code == "payload_too_big"
    assert repo.outbox_get(rig.conn, "r1") is None
    assert repo.last_messages(rig.conn, DIRECT_STR) == []


async def test_commands_store_queries(rig: Rig) -> None:
    for i, body in enumerate(["manzana roja", "pera verde"]):
        repo.upsert_message(
            rig.conn,
            repo.MessageIn(
                chat_jid=DIRECT_STR, wa_id=f"M{i}", ts=10 + i, sender_jid=DIRECT_STR, body=body
            ),
        )
    chats = await rig.commands("1", ListChats(limit=10))
    assert [c["jid"] for c in chats["chats"]] == [DIRECT_STR]
    assert chats["chats"][0]["unread_count"] == 2
    hist = await rig.commands("2", History(chat_jid=DIRECT_STR, n=1))
    assert [m["wa_id"] for m in hist["messages"]] == ["M1"]
    found = await rig.commands("3", Search(q="manzana", limit=10))
    assert [m["wa_id"] for m in found["messages"]] == ["M0"]
    assert await rig.commands("4", MarkRead(chat_jid=DIRECT_STR)) == {"changed": True}
    assert repo.list_chats(rig.conn)[0].unread_count == 0
    assert await rig.commands("5", MarkRead(chat_jid="nadie@s.whatsapp.net")) == {"changed": False}
    assert await rig.commands("6", Mute(chat_jid=DIRECT_STR, until=999)) == {"muted_until": 999}
    assert repo.list_chats(rig.conn)[0].muted_until == 999


async def test_commands_sqlite_error_becomes_internal(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr("wsp.daemon.outbox.repo.list_chats", boom)
    with pytest.raises(CommandError) as e:
        await rig.commands("1", ListChats(limit=5))
    assert e.value.code == "internal"
