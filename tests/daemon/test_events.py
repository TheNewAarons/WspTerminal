"""events.translate y Bridge: traducción, persistir-antes-de-emitir, duplicados, errores."""

import logging
import sqlite3
from typing import Any

import pytest

from tests.daemon.fakes import (
    DIRECT,
    DIRECT_STR,
    GROUP,
    GROUP_STR,
    ext_msg,
    history_ev,
    history_msg,
    jid,
    message_ev,
    ns,
    proto_msg,
    receipt_ev,
    text_msg,
)
from wsp.daemon.events import Bridge, ContactSeen, Item, Untranslatable, translate
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
from wsp.store.writer import Writer


def payloads(items: list[Any]) -> list[Any]:
    return [i.payload for i in items if isinstance(i, Item)]


def one(kind: str, ev: Any) -> Any:
    out = payloads(translate(kind, ev))
    assert len(out) == 1
    return out[0]


# ---- translate (pura) ---------------------------------------------------------------------


def test_conversation() -> None:
    p = one("MessageEv", message_ev(text_msg("hola")))
    assert isinstance(p, MessageNew)
    assert (p.chat_jid, p.wa_id, p.body, p.kind) == (DIRECT_STR, "A1", "hola", "text")
    assert p.sender_jid == DIRECT_STR
    assert not p.from_me


def test_from_me_has_no_sender() -> None:
    p = one("MessageEv", message_ev(text_msg("yo"), from_me=True))
    assert isinstance(p, MessageNew)
    assert p.from_me and p.sender_jid is None


def test_extended_text_and_reply() -> None:
    p = one("MessageEv", message_ev(ext_msg("largo")))
    assert isinstance(p, MessageNew)
    assert (p.kind, p.body, p.quoted_wa_id) == ("text", "largo", None)
    r = one("MessageEv", message_ev(ext_msg("resp", stanza="Q9")))
    assert isinstance(r, MessageNew)
    assert (r.kind, r.body, r.quoted_wa_id) == ("reply", "resp", "Q9")


def test_millisecond_timestamp_is_normalized() -> None:
    p = one("MessageEv", message_ev(text_msg("x"), ts=1_700_000_000_123))
    assert isinstance(p, MessageNew)
    assert p.ts == 1_700_000_000


@pytest.mark.parametrize(
    ("field", "kind"),
    [
        ("imageMessage", "image"),
        ("videoMessage", "video"),
        ("audioMessage", "audio"),
        ("documentMessage", "document"),
        ("stickerMessage", "sticker"),
        ("contactMessage", "contact"),
        ("locationMessage", "location"),
    ],
)
def test_media_with_caption(field: str, kind: str) -> None:
    p = one("MessageEv", message_ev(ns(**{field: ns(caption="mira")})))
    assert isinstance(p, MessageNew)
    assert (p.kind, p.body) == (kind, "mira")


def test_media_without_caption_has_no_body() -> None:
    p = one("MessageEv", message_ev(ns(imageMessage=ns(caption=""))))
    assert isinstance(p, MessageNew)
    assert (p.kind, p.body) == ("image", None)


def test_wrapped_message_is_unwrapped() -> None:
    wrapped = ns(ephemeralMessage=ns(message=ns(viewOnceMessage=ns(message=text_msg("dentro")))))
    p = one("MessageEv", message_ev(wrapped))
    assert isinstance(p, MessageNew)
    assert p.body == "dentro"


def test_reaction() -> None:
    msg = ns(reactionMessage=ns(key=ns(ID="T1"), text="👍"))
    p = one("MessageEv", message_ev(msg))
    assert isinstance(p, Reaction)
    assert (p.wa_id, p.emoji, p.sender_jid) == ("T1", "👍", DIRECT_STR)


def test_reaction_from_me_uses_me_jid() -> None:
    msg = ns(reactionMessage=ns(key=ns(ID="T1"), text=""))
    out = payloads(translate("MessageEv", message_ev(msg, from_me=True), "yo@s.whatsapp.net"))
    assert isinstance(out[0], Reaction)
    assert (out[0].sender_jid, out[0].emoji) == ("yo@s.whatsapp.net", "")


def test_reaction_without_target_is_untranslatable() -> None:
    with pytest.raises(Untranslatable):
        translate("MessageEv", message_ev(ns(reactionMessage=ns(key=ns(ID=""), text="x"))))


def test_edit() -> None:
    p = one("MessageEv", message_ev(proto_msg(14, "T1", edited=text_msg("nuevo"))))
    assert isinstance(p, MessageEdited)
    assert (p.wa_id, p.body) == ("T1", "nuevo")


def test_revoke() -> None:
    p = one("MessageEv", message_ev(proto_msg(0, "T1")))
    assert isinstance(p, MessageDeleted)
    assert p.wa_id == "T1"


def test_unknown_protocol_message_is_untranslatable() -> None:
    with pytest.raises(Untranslatable):
        translate("MessageEv", message_ev(proto_msg(5, "T1")))


def test_status_broadcast_is_ignored() -> None:
    assert translate("MessageEv", message_ev(text_msg("x"), chat=jid("status", "broadcast"))) == []


def test_pushname_yields_contact_seen() -> None:
    out = translate("MessageEv", message_ev(text_msg("x"), pushname="Juan"))
    assert out[0] == ContactSeen(DIRECT_STR, "Juan")
    assert isinstance(out[1], Item)


def test_group_message_sender_is_participant() -> None:
    sender = jid("5215522222222")
    p = one("MessageEv", message_ev(text_msg("x"), chat=GROUP, sender=sender))
    assert isinstance(p, MessageNew)
    assert (p.chat_jid, p.sender_jid) == (GROUP_STR, "5215522222222@s.whatsapp.net")


@pytest.mark.parametrize(("rtype", "status"), [(1, "delivered"), (4, "read")])
def test_receipts(rtype: int, status: str) -> None:
    out = payloads(translate("ReceiptEv", receipt_ev(rtype, ["A", "", "B"])))
    assert [(o.wa_id, o.status) for o in out] == [("A", status), ("B", status)]
    assert all(isinstance(o, MessageStatus) for o in out)


@pytest.mark.parametrize("rtype", [2, 3, 5, 9])
def test_other_receipts_ignored(rtype: int) -> None:
    with pytest.raises(Untranslatable):
        translate("ReceiptEv", receipt_ev(rtype, ["A"]))


def test_presence_available_and_unavailable() -> None:
    a = one("PresenceEv", ns(From=DIRECT, Unavailable=False))
    assert isinstance(a, Presence) and a.state == "available"
    u = one("PresenceEv", ns(From=DIRECT, Unavailable=True, LastSeen=1_700_000_000))
    assert isinstance(u, Presence)
    assert (u.state, u.last_seen) == ("unavailable", 1_700_000_000)


def test_presence_without_jid_is_untranslatable() -> None:
    with pytest.raises(Untranslatable):
        translate("PresenceEv", ns(From=None))


def test_chat_presence() -> None:
    src = ns(Sender=DIRECT, Chat=GROUP)
    c = one("ChatPresenceEv", ns(MessageSource=src, State=1))
    assert isinstance(c, Presence)
    assert (c.jid, c.state) == (DIRECT_STR, "composing")
    p = one("ChatPresenceEv", ns(MessageSource=src, State=2))
    assert isinstance(p, Presence) and p.state == "paused"
    with pytest.raises(Untranslatable):
        translate("ChatPresenceEv", ns(MessageSource=src, State=7))


def test_group_info_and_joined_group() -> None:
    info = ns(JID=GROUP, Name=ns(Name="Familia"))
    g = one("GroupInfoEv", ns(GroupInfo=info))
    assert isinstance(g, ChatUpdated)
    assert (g.jid, g.kind, g.title) == (GROUP_STR, "group", "Familia")
    j = one("JoinedGroupEv", ns(GroupInfo=ns(JID=GROUP, GroupName=ns(Name="Otro"))))
    assert isinstance(j, ChatUpdated) and j.title == "Otro"
    nameless = one("GroupInfoEv", ns(GroupInfo=ns(JID=GROUP)))
    assert isinstance(nameless, ChatUpdated) and nameless.title is None


def test_group_without_jid_is_untranslatable() -> None:
    with pytest.raises(Untranslatable):
        translate("GroupInfoEv", ns(GroupInfo=ns(JID=None)))


def test_history_translates_without_emit_or_unread() -> None:
    conv = ns(
        ID=GROUP_STR,
        name="Fam",
        messages=[
            history_msg("H1", text_msg("uno"), participant="5215522222222@s.whatsapp.net"),
            history_msg("H2", text_msg("dos"), from_me=True),
            history_msg("H3", proto_msg(0, "H1")),  # revoke: se salta
            ns(message=ns()),  # sin `message`: se salta
        ],
    )
    out = translate("HistorySyncEv", history_ev([conv, ns(ID="status@broadcast", messages=[])]))
    items = [i for i in out if isinstance(i, Item)]
    assert len(items) == 3
    assert all(not i.emit for i in items)
    assert all(not i.count_unread for i in items[1:])
    assert isinstance(items[0].payload, ChatUpdated) and items[0].payload.title == "Fam"
    first = items[1].payload
    assert isinstance(first, MessageNew)
    assert first.sender_jid == "5215522222222@s.whatsapp.net"


def test_unknown_kind_is_untranslatable() -> None:
    with pytest.raises(Untranslatable) as e:
        translate("WeirdEv", ns())
    assert e.value.kind == "WeirdEv"


# ---- Bridge -------------------------------------------------------------------------------


class Collector:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.events: list[Event] = []
        self.seen_in_db: list[bool] = []

    def __call__(self, event: Event) -> None:
        self.events.append(event)
        if isinstance(event, MessageNew):
            row = self.conn.execute(
                "SELECT 1 FROM messages WHERE chat_jid = ? AND wa_id = ?",
                (event.chat_jid, event.wa_id),
            ).fetchone()
            self.seen_in_db.append(row is not None)


@pytest.fixture
def col(conn: sqlite3.Connection) -> Collector:
    return Collector(conn)


@pytest.fixture
def bridge(writer: Writer, col: Collector) -> Bridge:
    return Bridge(writer, col)


async def test_persist_before_emit(bridge: Bridge, col: Collector) -> None:
    await bridge.handle("MessageEv", message_ev(text_msg("hola"), pushname="Juan"))
    assert len(col.events) == 1
    assert col.seen_in_db == [True]
    row = col.conn.execute("SELECT push_name FROM contacts WHERE jid = ?", (DIRECT_STR,)).fetchone()
    assert row["push_name"] == "Juan"


async def test_duplicate_is_not_emitted_twice(bridge: Bridge, col: Collector) -> None:
    ev = message_ev(text_msg("hola"))
    await bridge.handle("MessageEv", ev)
    await bridge.handle("MessageEv", ev)
    assert len(col.events) == 1
    chat = repo.list_chats(col.conn)[0]
    assert chat.unread_count == 1


async def test_db_error_does_not_emit_nor_raise(
    bridge: Bridge, col: Collector, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("wsp.daemon.events.repo.upsert_message", boom)
    await bridge.handle("MessageEv", message_ev(text_msg("hola")))
    assert col.events == []
    assert bridge.stats["db_error"] == 1


async def test_edit_and_revoke_apply_and_emit(bridge: Bridge, col: Collector) -> None:
    await bridge.handle("MessageEv", message_ev(text_msg("viejo")))
    await bridge.handle("MessageEv", message_ev(proto_msg(14, "A1", text_msg("nuevo")), wa_id="E1"))
    row = repo.last_messages(col.conn, DIRECT_STR)[0]
    assert (row.body, row.edited_at is not None) == ("nuevo", True)
    assert any(isinstance(e, MessageEdited) for e in col.events)
    await bridge.handle("MessageEv", message_ev(proto_msg(0, "A1"), wa_id="R1"))
    assert repo.last_messages(col.conn, DIRECT_STR)[0].deleted_at is not None
    assert any(isinstance(e, MessageDeleted) for e in col.events)


async def test_edit_by_other_author_is_rejected(bridge: Bridge, col: Collector) -> None:
    await bridge.handle("MessageEv", message_ev(text_msg("viejo")))
    n = len(col.events)
    other = message_ev(
        proto_msg(14, "A1", text_msg("hack")), wa_id="E1", sender=jid("5215599999999")
    )
    await bridge.handle("MessageEv", other)
    assert repo.last_messages(col.conn, DIRECT_STR)[0].body == "viejo"
    assert len(col.events) == n


async def test_reaction_persisted_and_emitted(bridge: Bridge, col: Collector) -> None:
    await bridge.handle("MessageEv", message_ev(text_msg("hola")))
    react = ns(reactionMessage=ns(key=ns(ID="A1"), text="🔥"))
    await bridge.handle("MessageEv", message_ev(react, wa_id="R1"))
    emoji = col.conn.execute("SELECT emoji FROM reactions").fetchone()["emoji"]
    assert emoji == "🔥"
    assert isinstance(col.events[-1], Reaction)


async def test_receipts_advance_status_once(bridge: Bridge, col: Collector) -> None:
    out = message_ev(text_msg("yo"), from_me=True, wa_id="M1")
    await bridge.handle("MessageEv", out)
    await bridge.handle("ReceiptEv", receipt_ev(1, ["M1"]))
    await bridge.handle("ReceiptEv", receipt_ev(1, ["M1"]))  # repetido: sin cambio
    await bridge.handle("ReceiptEv", receipt_ev(4, ["M1"]))
    await bridge.handle("ReceiptEv", receipt_ev(5, ["M1"]))  # READ_SELF: ignorado
    statuses = [e.status for e in col.events if isinstance(e, MessageStatus)]
    assert statuses == ["delivered", "read"]
    assert bridge.stats["ignored"] == 1
    assert repo.last_messages(col.conn, DIRECT_STR)[0].status == "read"


async def test_presence_events_are_emitted_not_persisted(bridge: Bridge, col: Collector) -> None:
    await bridge.handle("PresenceEv", ns(From=DIRECT, Unavailable=False))
    await bridge.handle("ChatPresenceEv", ns(MessageSource=ns(Sender=DIRECT, Chat=DIRECT), State=1))
    assert [type(e) for e in col.events] == [Presence, Presence]
    assert repo.list_chats(col.conn) == []


async def test_group_info_persists_chat(bridge: Bridge, col: Collector) -> None:
    await bridge.handle("GroupInfoEv", ns(GroupInfo=ns(JID=GROUP, Name=ns(Name="Familia"))))
    chats = repo.list_chats(col.conn)
    assert [(c.jid, c.kind, c.title) for c in chats] == [(GROUP_STR, "group", "Familia")]
    assert isinstance(col.events[0], ChatUpdated)


async def test_history_sync_persists_silently(bridge: Bridge, col: Collector) -> None:
    conv = ns(
        ID=DIRECT_STR,
        name="Juan",
        messages=[history_msg("H1", text_msg("a")), history_msg("H2", text_msg("b"), ts=2_000)],
    )
    await bridge.handle("HistorySyncEv", history_ev([conv]))
    assert col.events == []
    rows = repo.last_messages(col.conn, DIRECT_STR)
    assert sorted(r.wa_id for r in rows) == ["H1", "H2"]
    chat = repo.list_chats(col.conn)[0]
    assert chat.unread_count == 0
    assert chat.title == "Juan"


async def test_status_broadcast_is_dropped(bridge: Bridge, col: Collector) -> None:
    await bridge.handle(
        "MessageEv", message_ev(text_msg("historia"), chat=jid("status", "broadcast"))
    )
    assert col.events == []
    assert repo.list_chats(col.conn) == []


async def test_untranslatable_is_ignored_and_log_has_no_content(
    bridge: Bridge, col: Collector, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    secret = "SECRETO-12345"  # noqa: S105
    await bridge.handle("MessageEv", message_ev(ns(pollCreationMessage=ns(text=secret))))
    await bridge.handle("PushNameEv", ns(text=secret))
    assert col.events == []
    assert bridge.stats["ignored"] == 2
    assert secret not in caplog.text
    assert "5215511111111" not in caplog.text


async def test_malformed_event_is_counted_without_raising(
    bridge: Bridge, col: Collector, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await bridge.handle("MessageEv", ns(Info=None, Message=text_msg("SECRETO-99")))
    assert bridge.stats["bad_event"] == 1
    assert col.events == []
    assert "SECRETO-99" not in caplog.text


async def test_invalid_payload_is_counted_as_bad_event(bridge: Bridge, col: Collector) -> None:
    # JID de 200 caracteres: falla la validación pydantic del modelo del protocolo.
    long_chat = jid("1" * 200)
    await bridge.handle("MessageEv", message_ev(text_msg("x"), chat=long_chat))
    assert bridge.stats["bad_event"] == 1
    assert col.events == []
