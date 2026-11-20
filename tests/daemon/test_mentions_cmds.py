"""Menciones en el daemon: validación en Commands, comando Members y envío al cliente."""

import sqlite3

import pytest

from tests.daemon.fakes import DIRECT_STR, GROUP_STR
from tests.daemon.test_outbox import Rig
from wsp.daemon.config import OutboxConfig
from wsp.daemon.outbox import Commands
from wsp.protocol.models import Members, SendReply, SendText
from wsp.protocol.server import CommandError
from wsp.store import repo
from wsp.store.writer import Writer

ANA = "5215511110001@s.whatsapp.net"
LUIS = "5215522220002@s.whatsapp.net"


CFG = OutboxConfig(rate_per_sec=10, burst=10, max_attempts=3, retry_base=1.0)


class MRig(Rig):
    """Rig con `Commands` que lee los miembros del FakeClient."""

    def __init__(self, conn: sqlite3.Connection, writer: Writer) -> None:
        super().__init__(conn, writer, CFG)
        self.commands = Commands(writer, self.outbox, self.client.group_members)


@pytest.fixture
def rig(conn: sqlite3.Connection, writer: Writer) -> MRig:
    return MRig(conn, writer)


async def test_send_text_with_mentions_reaches_client(rig: MRig) -> None:
    await rig.commands(
        "r1",
        SendText(chat_jid=GROUP_STR, text="hola @5215511110001", mentions=[ANA]),
    )
    await rig.drain()
    assert rig.client.mentions_sent == [[ANA]]


async def test_send_text_without_mentions_passes_none(rig: MRig) -> None:
    await rig.commands("r1", SendText(chat_jid=DIRECT_STR, text="hola"))
    await rig.drain()
    assert rig.client.mentions_sent == [None]


async def test_send_reply_with_mentions_reaches_client(rig: MRig) -> None:
    await rig.commands(
        "r1",
        SendReply(chat_jid=GROUP_STR, quoted_wa_id="Q1", text="va @5215511110001", mentions=[ANA]),
    )
    await rig.drain()
    assert rig.client.mentions_sent == [[ANA]]


async def test_mention_missing_in_text_rejected(rig: MRig) -> None:
    with pytest.raises(CommandError) as ei:
        await rig.commands("r1", SendText(chat_jid=GROUP_STR, text="hola", mentions=[ANA]))
    assert ei.value.code == "bad_payload"
    assert await rig.drain() == 0


async def test_reply_mention_missing_in_text_rejected(rig: MRig) -> None:
    with pytest.raises(CommandError) as ei:
        await rig.commands(
            "r1",
            SendReply(chat_jid=GROUP_STR, quoted_wa_id="Q1", text="hola", mentions=[ANA]),
        )
    assert ei.value.code == "bad_payload"


async def test_members_returns_names_from_store(rig: MRig) -> None:
    rig.client.members[GROUP_STR] = [(ANA, None, None), (LUIS, "Luis WA", None)]
    repo.upsert_contact(rig.conn, ANA, push_name="Ana Push")
    res = await rig.commands("r1", Members(chat_jid=GROUP_STR))
    assert res == {"members": [{"jid": ANA, "name": "Ana Push"}, {"jid": LUIS, "name": "Luis WA"}]}


async def test_members_lid_resolves_name_via_phone_jid(rig: MRig) -> None:
    lid = "258677357432950@lid"
    rig.client.members[GROUP_STR] = [(lid, None, ANA)]
    repo.upsert_contact(rig.conn, ANA, push_name="Ana Guardada")
    res = await rig.commands("r1", Members(chat_jid=GROUP_STR))
    assert res == {"members": [{"jid": lid, "name": "Ana Guardada"}]}


async def test_members_unknown_name_is_none(rig: MRig) -> None:
    rig.client.members[GROUP_STR] = [(ANA, None, None)]
    res = await rig.commands("r1", Members(chat_jid=GROUP_STR))
    assert res == {"members": [{"jid": ANA, "name": None}]}


async def test_members_not_group_rejected(rig: MRig) -> None:
    with pytest.raises(CommandError) as ei:
        await rig.commands("r1", Members(chat_jid=DIRECT_STR))
    assert ei.value.code == "not_found"


async def test_members_without_provider_rejected(conn: sqlite3.Connection, writer: Writer) -> None:
    r = Rig(conn, writer, CFG)
    with pytest.raises(CommandError) as ei:
        await r.commands("r1", Members(chat_jid=GROUP_STR))
    assert ei.value.code == "not_found"


async def test_members_provider_failure_is_internal(
    conn: sqlite3.Connection, writer: Writer
) -> None:
    r = Rig(conn, writer, CFG)

    async def boom(chat_jid: str) -> list[tuple[str, str | None]]:
        raise RuntimeError("secreto")

    cmds = Commands(writer, r.outbox, boom)
    with pytest.raises(CommandError) as ei:
        await cmds("r1", Members(chat_jid=GROUP_STR))
    assert ei.value.code == "internal"
    assert "secreto" not in ei.value.message


def test_check_mentions_requires_word_boundary() -> None:
    from wsp.daemon.outbox import _check_mentions

    _check_mentions("hola @5215511110001 ok", ["5215511110001@s.whatsapp.net"])
    with pytest.raises(CommandError):
        _check_mentions("hola @52155111100019", ["5215511110001@s.whatsapp.net"])


@pytest.mark.parametrize("bad", ["x y@s.whatsapp.net", "123@g.us", "a@b@s.whatsapp.net", "@lid"])
def test_send_text_rejects_malformed_mention_jid(bad: str) -> None:
    from pydantic import ValidationError

    from wsp.protocol.models import SendText

    with pytest.raises(ValidationError):
        SendText(chat_jid="1@g.us", text="@x", mentions=[bad])
