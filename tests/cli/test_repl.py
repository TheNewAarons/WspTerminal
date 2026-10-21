"""REPL contra un daemon falso (Server real + handler falso) con `create_pipe_input`."""

import asyncio
import io
import stat
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input import PipeInput, create_pipe_input
from prompt_toolkit.output import DummyOutput

from wsp.cli import repl
from wsp.cli.render import Palette
from wsp.cli.repl import Repl, run, run_loop
from wsp.protocol.client import Client
from wsp.protocol.models import (
    AccountState,
    BackfillEnd,
    BackfillStart,
    ChatUpdated,
    HandledCommand,
    History,
    ListChats,
    MarkRead,
    MessageNew,
    MessageStatus,
    SendText,
)
from wsp.protocol.server import CommandError, Server

JUAN = "111@s.whatsapp.net"
FAMILIA = "999@g.us"
ANA = "222@s.whatsapp.net"
T0 = 1_792_504_920  # 2026-10-20 14:02 UTC
TIMEOUT = 3.0


async def until(pred: Callable[[], bool], timeout: float = TIMEOUT) -> None:  # noqa: ASYNC109
    async with asyncio.timeout(timeout):
        while not pred():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


def chat(jid: str, title: str, kind: str = "direct", unread: int = 0) -> ChatUpdated:
    return ChatUpdated(jid=jid, kind=kind, title=title, unread_count=unread)  # type: ignore[arg-type]


def msg(wa_id: str, ts: int = T0, chat_jid: str = JUAN, **kw: Any) -> MessageNew:
    return MessageNew(chat_jid=chat_jid, wa_id=wa_id, ts=ts, **kw)


class FakeDaemon:
    """Handler falso: registra comandos y responde con chats/historial fijos."""

    def __init__(self) -> None:
        self.cmds: list[HandledCommand] = []
        self.send_error: CommandError | None = None
        self.chats = [chat(JUAN, "Juan", unread=2), chat(FAMILIA, "Familia", "group")]
        self.history = [msg("h1", body="antes"), msg("h2", body="hola", sender_jid=None)]

    async def __call__(self, rid: str, cmd: HandledCommand) -> Mapping[str, Any]:
        self.cmds.append(cmd)
        match cmd:
            case ListChats():
                return {"chats": [c.model_dump(mode="json") for c in self.chats]}
            case History():
                return {"messages": [m.model_dump(mode="json") for m in self.history]}
            case SendText():
                if self.send_error is not None:
                    raise self.send_error
                return {"state": "pending", "wa_id": "local-" + rid}
            case _:
                return {}


async def no_backfill(since: int) -> tuple[list[MessageNew], bool]:
    return [], False


@pytest.fixture
def daemon() -> FakeDaemon:
    return FakeDaemon()


@pytest.fixture
async def server(short_path: Path, daemon: FakeDaemon) -> AsyncIterator[Server]:
    srv = Server(short_path / "s.sock", daemon, no_backfill)
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture
async def client(server: Server, short_path: Path) -> AsyncIterator[Client]:
    async with Client(short_path / "s.sock", kind="cli") as c:
        await c.wait_connected(TIMEOUT)
        yield c


@pytest.fixture
def out() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def pipe() -> Any:
    with create_pipe_input() as p:
        yield p


def make_session(pipe: PipeInput) -> PromptSession[str]:
    return PromptSession(input=pipe, output=DummyOutput())


async def pump_events(client: Client, r: Repl) -> asyncio.Task[None]:
    async def go() -> None:
        async for ev in client.events():
            r.handle(ev)

    return asyncio.create_task(go())


# ---- manejo de líneas ------------------------------------------------------------------------


async def test_text_without_open_chat_shows_help(client: Client, out: io.StringIO) -> None:
    r = Repl(client, out, Palette(False))
    assert await r.handle_line("hola") is True
    assert "Sin chat abierto" in out.getvalue()
    assert r.prompt() == "› "


async def test_chats_open_and_send(client: Client, out: io.StringIO, daemon: FakeDaemon) -> None:
    r = Repl(client, out, Palette(False), tz=UTC)
    await r.handle_line("/chats")
    assert "1  Juan  (2 sin leer)" in out.getvalue()
    assert "2  Familia  (grupo)" in out.getvalue()
    await r.handle_line("/open 1")
    assert r.prompt() == "Juan › "
    text = out.getvalue()
    assert "── martes 20 oct 2026 ──" in text
    assert "[14:02] Juan › antes" in text
    assert any(isinstance(c, MarkRead) and c.chat_jid == JUAN for c in daemon.cmds)
    await r.handle_line("hola desde wsp")
    sent = [c for c in daemon.cmds if isinstance(c, SendText)]
    assert [(c.chat_jid, c.text) for c in sent] == [(JUAN, "hola desde wsp")]
    await r.handle_line("/back")
    assert r.prompt() == "› "


async def test_open_by_fuzzy_name(client: Client, out: io.StringIO) -> None:
    r = Repl(client, out, Palette(False))
    await r.handle_line("/open famlia")
    assert r.open_jid == FAMILIA
    await r.handle_line("/open zzzzzz")
    assert "no encuentro el chat" in out.getvalue()


async def test_bad_commands_and_stubs(client: Client, out: io.StringIO) -> None:
    r = Repl(client, out, Palette(False))
    await r.handle_line("/nada")
    await r.handle_line("/chats x")
    await r.handle_line("/open")
    await r.handle_line("/search algo")
    await r.handle_line("/history")
    text = out.getvalue()
    assert "comando desconocido: /nada" in text
    assert "uso: /chats [n]" in text
    assert "uso: /open" in text
    assert "/search: aún no disponible" in text
    assert "Sin chat abierto" in text
    assert await r.handle_line("/help") is True
    assert "/quit" in out.getvalue()
    assert await r.handle_line("/quit") is False


async def test_send_error_is_reported_without_traceback(
    client: Client, out: io.StringIO, daemon: FakeDaemon
) -> None:
    daemon.send_error = CommandError("payload_too_big", "mensaje demasiado grande")
    r = Repl(client, out, Palette(False))
    r.open_jid = JUAN
    await r.handle_line("hola")
    assert "✗ payload_too_big: mensaje demasiado grande" in out.getvalue()
    assert "Traceback" not in out.getvalue()


# ---- eventos entrantes -----------------------------------------------------------------------


async def test_live_message_and_status_lines(
    client: Client, server: Server, out: io.StringIO
) -> None:
    r = Repl(client, out, Palette(False), tz=UTC)
    task = await pump_events(client, r)
    server.broadcast(chat(JUAN, "Juan"))
    server.broadcast(chat(FAMILIA, "Familia", "group"))
    server.broadcast(msg("m1", body="hola"))
    server.broadcast(msg("m2", chat_jid=FAMILIA, sender_jid=ANA, body="buenas"))
    await until(lambda: "buenas" in out.getvalue())
    lines = out.getvalue().splitlines()
    assert "[14:02] Juan › hola" in lines
    assert "[14:02] Familia / 222 › buenas" in lines
    r.open_jid = JUAN
    server.broadcast(MessageStatus(chat_jid=JUAN, wa_id="m1", status="delivered"))
    await until(lambda: "entregado" in out.getvalue())
    assert out.getvalue().splitlines()[-1] == "  ✓✓ entregado"
    server.broadcast(MessageStatus(chat_jid=FAMILIA, wa_id="m2", status="read"))
    server.broadcast(MessageStatus(chat_jid=FAMILIA, wa_id="m2", status="failed", error="x"))
    await until(lambda: "fallido" in out.getvalue())
    assert "✗ fallido: x (Familia)" in out.getvalue()
    assert "leído" not in out.getvalue()  # estado de un chat no abierto: no se imprime
    task.cancel()


async def test_incoming_escape_sequences_are_neutralised(
    client: Client, server: Server, out: io.StringIO
) -> None:
    r = Repl(client, out, Palette(False), tz=UTC)
    task = await pump_events(client, r)
    server.broadcast(msg("m1", body="\x1b]0;pwned\x07\x1b[2Jhola"))
    await until(lambda: "hola" in out.getvalue())
    assert "\x1b" not in out.getvalue()
    task.cancel()


async def test_backfill_between_separators_and_dedupe() -> None:
    out = io.StringIO()
    r = Repl(None, out, Palette(False), tz=UTC)  # type: ignore[arg-type]
    r.handle(BackfillStart(since=T0))
    r.handle(msg("a", body="uno"))
    r.handle(msg("b", ts=T0 + 60, body="dos"))
    assert out.getvalue() == ""
    r.handle(BackfillEnd(count=2))
    lines = out.getvalue().splitlines()
    assert lines[0] == "── reconectado, 2 mensajes nuevos ──"
    assert "[14:02] 111 › uno" in lines
    assert "[14:03] 111 › dos" in lines
    assert lines[-1] == "── fin del relleno ──"
    n = len(lines)
    r.handle(msg("a", body="uno"))  # repetido: no se reimprime
    assert len(out.getvalue().splitlines()) == n


async def test_edit_delete_and_quote_render() -> None:
    out = io.StringIO()
    r = Repl(None, out, Palette(False), tz=UTC)  # type: ignore[arg-type]
    r.handle(msg("a", body="original"))
    r.handle(msg("b", body="sí", from_me=True, status="sent", quoted_wa_id="a"))
    from wsp.protocol.models import MessageDeleted, MessageEdited

    r.handle(MessageEdited(chat_jid=JUAN, wa_id="a", body="corregido", edited_at=T0 + 5))
    r.handle(MessageDeleted(chat_jid=JUAN, wa_id="a", deleted_at=T0 + 9))
    text = out.getvalue()
    assert "  ↳ 111: original" in text
    assert "[14:02] tú › sí ✓" in text
    assert "[14:02] 111 › corregido (editado)" in text
    assert "[14:02] 111 › (mensaje eliminado)" in text


async def test_account_state_notices() -> None:
    out = io.StringIO()
    r = Repl(None, out, Palette(False))  # type: ignore[arg-type]
    r.handle(AccountState(state="disconnected", reason="red"))
    r.handle(AccountState(state="connected"))
    assert "── cuenta disconnected (red) ──" in out.getvalue()
    assert "── cuenta conectada ──" in out.getvalue()


# ---- bucle con teclado simulado --------------------------------------------------------------


async def test_loop_sends_text_and_quits(
    client: Client, out: io.StringIO, daemon: FakeDaemon, pipe: PipeInput
) -> None:
    r = Repl(client, out, Palette(False))
    r.open_jid = JUAN
    pipe.send_text("hola\r/quit\r")
    assert await run_loop(r, make_session(pipe)) == 0
    assert [c.text for c in daemon.cmds if isinstance(c, SendText)] == ["hola"]


async def test_ctrl_c_clears_line_and_ctrl_d_exits(
    client: Client, out: io.StringIO, daemon: FakeDaemon, pipe: PipeInput
) -> None:
    r = Repl(client, out, Palette(False))
    r.open_jid = JUAN
    pipe.send_text("borrador\x03")  # Ctrl+C: la línea se descarta, no se envía ni sale
    pipe.send_text("real\r")
    pipe.send_text("\x04")  # Ctrl+D con la línea vacía: salir
    assert await run_loop(r, make_session(pipe)) == 0
    assert [c.text for c in daemon.cmds if isinstance(c, SendText)] == ["real"]


# ---- run(): arranque -------------------------------------------------------------------------


async def test_run_end_to_end_with_incoming_over_prompt(
    server: Server,
    short_path: Path,
    pipe: PipeInput,
    daemon: FakeDaemon,
    wsp_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server.broadcast(AccountState(state="connected"))
    task = asyncio.create_task(
        run(pal=Palette(False), session=make_session(pipe), socket=short_path / "s.sock", tz=UTC)
    )
    await until(lambda: server.client_count == 1)
    server.broadcast(chat(JUAN, "Juan"))
    server.broadcast(msg("m1", body="entrante"))
    pipe.send_text("/open 1\rhola\r/quit\r")
    assert await asyncio.wait_for(task, TIMEOUT) == 0
    assert [c.text for c in daemon.cmds if isinstance(c, SendText)] == ["hola"]


async def test_run_without_daemon_offers_install_or_status(
    short_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(repl, "CONNECT_TIMEOUT", 0.2)
    calls: list[str] = []
    monkeypatch.setattr(repl.daemon_cmd, "status", lambda out: calls.append("status") or 0)
    monkeypatch.setattr(repl.daemon_cmd, "install", lambda out: calls.append("install") or 0)
    for choice in ("s", "i", ""):
        code = await run(
            pal=Palette(False), socket=short_path / "no.sock", ask=lambda _p, c=choice: c
        )
        assert code == repl.EXIT_UNREACHABLE
    assert calls == ["status", "install"]
    text = capsys.readouterr().out
    assert "wsp daemon install" in text and "wsp daemon status" in text
    assert "Traceback" not in text


async def test_run_unlinked_account_points_to_pair(
    server: Server, short_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server.broadcast(AccountState(state="logged_out"))
    code = await run(pal=Palette(False), socket=short_path / "s.sock")
    assert code == repl.EXIT_UNLINKED
    assert "wsp pair" in capsys.readouterr().out


def test_history_file_is_private(wsp_home: Path) -> None:
    repl._history_file()
    path = wsp_home / "state" / "history"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_history_persists_only_commands(wsp_home: Path) -> None:
    h = repl._history_file()
    h.store_string("mensaje privado")
    h.store_string("/chats")
    text = (wsp_home / "state" / "history").read_text()
    assert "/chats" in text and "privado" not in text


async def test_extreme_ts_does_not_kill_pump() -> None:
    out = io.StringIO()
    r = Repl(None, out, Palette(False), tz=UTC)  # type: ignore[arg-type]
    r.handle(msg("a", ts=2**63 - 1, body="x"))
    assert "› x" in out.getvalue()


async def test_malformed_reply_is_reported(
    client: Client, out: io.StringIO, daemon: FakeDaemon
) -> None:
    daemon.chats = 5  # type: ignore[assignment]
    r = Repl(client, out, Palette(False))
    await r.handle_line("/chats")
    assert "Traceback" not in out.getvalue()
