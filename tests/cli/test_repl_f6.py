"""Comandos de la fase 6 en el Repl: /r, /react, /search, /mute, /open ambiguo y notificaciones."""

import asyncio
import io
import time
from collections.abc import AsyncIterator, Mapping
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest

from wsp.cli.render import Palette
from wsp.cli.repl import Repl
from wsp.protocol.client import Client
from wsp.protocol.models import (
    ChatUpdated,
    HandledCommand,
    History,
    ListChats,
    MessageNew,
    Mute,
    React,
    Search,
    SendReply,
)
from wsp.protocol.server import Server

JUAN = "111@s.whatsapp.net"
FAMILIA = "999@g.us"
ANA = "222@s.whatsapp.net"
T0 = 1_792_504_920
TIMEOUT = 3.0


def chat(jid: str, title: str, kind: str = "direct", **kw: Any) -> ChatUpdated:
    return ChatUpdated(jid=jid, kind=kind, title=title, **kw)


def msg(wa_id: str, chat_jid: str = JUAN, ts: int = T0, **kw: Any) -> MessageNew:
    return MessageNew(chat_jid=chat_jid, wa_id=wa_id, ts=ts, **kw)


class FakeDaemon:
    def __init__(self) -> None:
        self.cmds: list[HandledCommand] = []
        self.chats = [chat(JUAN, "Juan"), chat(FAMILIA, "Familia", "group"), chat(ANA, "Ana")]
        self.history = [msg("h1", body="uno"), msg("h2", body="dos")]
        self.hits: list[MessageNew] = []

    async def __call__(self, rid: str, cmd: HandledCommand) -> Mapping[str, Any]:
        self.cmds.append(cmd)
        match cmd:
            case ListChats():
                return {"chats": [c.model_dump(mode="json") for c in self.chats]}
            case History():
                return {"messages": [m.model_dump(mode="json") for m in self.history]}
            case Search():
                return {"messages": [m.model_dump(mode="json") for m in self.hits]}
            case _:
                return {"state": "pending"}

    def of(self, kind: type[Any]) -> list[Any]:
        return [c for c in self.cmds if isinstance(c, kind)]


async def no_backfill(since: int) -> tuple[list[MessageNew], bool]:
    return [], False


@pytest.fixture
def daemon() -> FakeDaemon:
    return FakeDaemon()


@pytest.fixture
async def client(short_path: Path, daemon: FakeDaemon) -> AsyncIterator[Client]:
    srv = Server(short_path / "s.sock", daemon, no_backfill)
    await srv.start()
    async with Client(short_path / "s.sock", kind="cli") as c:
        await c.wait_connected(TIMEOUT)
        yield c
    await srv.stop()


@pytest.fixture
def out() -> io.StringIO:
    return io.StringIO()


class Notifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, title: str, body: str) -> bool:
        self.calls.append((title, body))
        return True


async def drain() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


# ---- /r y /react -----------------------------------------------------------------------------


async def test_reply_uses_wa_id_of_numbered_message(
    client: Client, out: io.StringIO, daemon: FakeDaemon
) -> None:
    r = Repl(client, out, Palette(False), tz=UTC)
    await r.handle_line("/open 1")
    assert "1" in out.getvalue()
    await r.handle_line("/r 2 gracias")
    (cmd,) = daemon.of(SendReply)
    assert (cmd.chat_jid, cmd.quoted_wa_id, cmd.text) == (JUAN, "h2", "gracias")


async def test_react_uses_wa_id_of_numbered_message(
    client: Client, out: io.StringIO, daemon: FakeDaemon
) -> None:
    r = Repl(client, out, Palette(False), tz=UTC)
    await r.handle_line("/open 1")
    await r.handle_line("/react 1 👍")
    (cmd,) = daemon.of(React)
    assert (cmd.chat_jid, cmd.wa_id, cmd.emoji) == (JUAN, "h1", "👍")


@pytest.mark.parametrize("line", ["/r 3 hola", "/react 9 👍"])
async def test_number_outside_view_is_reported(
    line: str, client: Client, out: io.StringIO, daemon: FakeDaemon
) -> None:
    r = Repl(client, out, Palette(False), tz=UTC)
    await r.handle_line("/open 1")
    await r.handle_line(line)
    assert "no hay mensaje" in out.getvalue() and "(1-2)" in out.getvalue()
    assert daemon.of(SendReply) == [] and daemon.of(React) == []


async def test_reply_without_open_chat(
    client: Client, out: io.StringIO, daemon: FakeDaemon
) -> None:
    r = Repl(client, out, Palette(False))
    await r.handle_line("/r 1 hola")
    assert "Sin chat abierto" in out.getvalue()
    assert daemon.of(SendReply) == []


async def test_live_message_gets_next_number(
    client: Client, out: io.StringIO, daemon: FakeDaemon
) -> None:
    r = Repl(client, out, Palette(False), tz=UTC)
    await r.handle_line("/open 1")
    r.handle(msg("h3", body="tres"))
    await r.handle_line("/r 3 ok")
    assert daemon.of(SendReply)[0].quoted_wa_id == "h3"


# ---- /search ---------------------------------------------------------------------------------


async def test_search_lists_and_open_jumps_to_chat(
    client: Client, out: io.StringIO, daemon: FakeDaemon
) -> None:
    daemon.hits = [msg("s1", FAMILIA, body="cena"), msg("s2", ANA, body="cena mañana")]
    r = Repl(client, out, Palette(False), tz=UTC)
    await r.handle_line("/chats")
    await r.handle_line("/search cena")
    assert daemon.of(Search)[0].q == "cena"
    assert "Familia · 20 oct 14:02" in out.getvalue()
    assert r.listing == [FAMILIA, ANA]
    await r.handle_line("/open 1")
    assert r.open_jid == FAMILIA


async def test_search_no_results(client: Client, out: io.StringIO) -> None:
    r = Repl(client, out, Palette(False))
    await r.handle_line("/search nada")
    assert "sin resultados" in out.getvalue()


# ---- /mute -----------------------------------------------------------------------------------


async def test_mute_sets_until_from_now(
    client: Client, out: io.StringIO, daemon: FakeDaemon
) -> None:
    r = Repl(client, out, Palette(False))
    before = int(time.time())
    await r.handle_line("/mute juan 8h")
    after = int(time.time())
    (cmd,) = daemon.of(Mute)
    assert cmd.chat_jid == JUAN
    assert before + 8 * 3600 <= cmd.until <= after + 8 * 3600
    assert r.chats[JUAN].muted_until == cmd.until
    assert "silenciado" in out.getvalue()


async def test_mute_off_sends_zero(client: Client, out: io.StringIO, daemon: FakeDaemon) -> None:
    daemon.chats[0] = chat(JUAN, "Juan", muted_until=T0 * 2)
    r = Repl(client, out, Palette(False))
    await r.handle_line("/mute juan off")
    assert daemon.of(Mute)[0].until == 0
    assert r.chats[JUAN].muted_until == 0
    assert "con sonido de nuevo" in out.getvalue()


async def test_mute_multiword_name(client: Client, out: io.StringIO, daemon: FakeDaemon) -> None:
    daemon.chats.append(chat("333@s.whatsapp.net", "Grupo del Trabajo"))
    r = Repl(client, out, Palette(False))
    await r.handle_line("/mute grupo del trabajo 30m")
    assert daemon.of(Mute)[0].chat_jid == "333@s.whatsapp.net"


async def test_mute_bad_duration_and_unknown_chat(
    client: Client, out: io.StringIO, daemon: FakeDaemon
) -> None:
    r = Repl(client, out, Palette(False))
    await r.handle_line("/mute juan 5x")
    await r.handle_line("/mute zzzzzz 1h")
    assert "duración inválida" in out.getvalue()
    assert "no encuentro el chat" in out.getvalue()
    assert daemon.of(Mute) == []


# ---- /open ambiguo ---------------------------------------------------------------------------


async def test_open_ambiguous_prints_top_and_sets_listing(
    client: Client, out: io.StringIO, daemon: FakeDaemon
) -> None:
    daemon.chats = [chat(f"{i}@s.whatsapp.net", f"Carlos {n}") for i, n in enumerate("abcdefg")]
    r = Repl(client, out, Palette(False))
    await r.handle_line("/open carlos")
    assert r.open_jid is None
    assert len(r.listing) == 5
    text = out.getvalue()
    assert "es ambiguo" in text and "/open <n>" in text
    first = r.listing[0]
    await r.handle_line("/open 1")
    assert r.open_jid == first


async def test_open_number_out_of_range(client: Client, out: io.StringIO) -> None:
    r = Repl(client, out, Palette(False))
    await r.handle_line("/open 9")  # sin listado previo: carga /chats y valida el rango
    assert "no hay chat 9 en la lista (1-3)" in out.getvalue()
    assert r.open_jid is None


# ---- notificaciones --------------------------------------------------------------------------


def notifying(out: io.StringIO, n: Notifier) -> Repl:
    r = Repl(None, out, Palette(False), tz=UTC, notifier=n)  # type: ignore[arg-type]
    r.chats[JUAN] = chat(JUAN, "Juan")
    r.chats[FAMILIA] = chat(FAMILIA, "Familia", "group")
    return r


async def test_notifies_closed_chat_message() -> None:
    n = Notifier()
    r = notifying(io.StringIO(), n)
    r.handle(msg("m1", body="hola"))
    await drain()
    assert n.calls == [("Juan", "hola")]


async def test_notifies_group_with_sender_prefix() -> None:
    n = Notifier()
    r = notifying(io.StringIO(), n)
    r.handle(msg("m1", FAMILIA, sender_jid=ANA, body="buenas"))
    await drain()
    assert n.calls == [("Familia", "222: buenas")]


async def test_no_notify_for_open_chat() -> None:
    n = Notifier()
    r = notifying(io.StringIO(), n)
    r.open_jid = JUAN
    r.handle(msg("m1", body="hola"))
    await drain()
    assert n.calls == []


async def test_no_notify_for_muted_chat() -> None:
    n = Notifier()
    r = notifying(io.StringIO(), n)
    r.chats[JUAN] = chat(JUAN, "Juan", muted_until=int(time.time()) + 3600)
    r.handle(msg("m1", body="hola"))
    await drain()
    assert n.calls == []


async def test_expired_mute_notifies() -> None:
    n = Notifier()
    r = notifying(io.StringIO(), n)
    r.chats[JUAN] = chat(JUAN, "Juan", muted_until=1)
    r.handle(msg("m1", body="hola"))
    await drain()
    assert len(n.calls) == 1


async def test_no_notify_for_own_or_deleted_or_repeated() -> None:
    n = Notifier()
    r = notifying(io.StringIO(), n)
    r.handle(msg("m1", body="mío", from_me=True))
    r.handle(msg("m2", body="borrado", deleted_at=T0 + 1))
    r.handle(msg("m3", body="una vez"))
    r.handle(msg("m3", body="una vez"))  # repetido: no vuelve a notificar
    await drain()
    assert n.calls == [("Juan", "una vez")]


async def test_no_notifier_means_no_error() -> None:
    r = Repl(None, io.StringIO(), Palette(False), tz=UTC)  # type: ignore[arg-type]
    r.handle(msg("m1", body="hola"))
    await drain()
