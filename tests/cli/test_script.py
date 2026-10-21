"""`wsp send` y `wsp tail` contra un daemon falso (Server real + handler falso)."""

import asyncio
import io
import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from wsp.cli import script
from wsp.cli.render import Palette
from wsp.protocol.models import (
    ChatUpdated,
    HandledCommand,
    ListChats,
    MessageNew,
    MessageStatus,
    SendText,
)
from wsp.protocol.server import CommandError, Server

JUAN = "111@s.whatsapp.net"
ANA = "222@s.whatsapp.net"
T0 = 1_792_504_920  # 2026-10-20 14:02 UTC
TIMEOUT = 3.0


async def until(pred: Any, timeout: float = TIMEOUT) -> None:  # noqa: ASYNC109
    async with asyncio.timeout(timeout):
        while not pred():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


def chat(jid: str, title: str) -> ChatUpdated:
    return ChatUpdated(jid=jid, kind="direct", title=title)


class FakeDaemon:
    def __init__(self) -> None:
        self.cmds: list[HandledCommand] = []
        self.chats = [chat(JUAN, "Juan"), chat(ANA, "Ana")]
        self.send_error: CommandError | None = None
        self.send_state = "pending"
        self.send_err_text: str | None = None
        self.follow_up: MessageStatus | None = None  # evento emitido tras el envío
        self.server: Server | None = None

    async def __call__(self, rid: str, cmd: HandledCommand) -> Mapping[str, Any]:
        self.cmds.append(cmd)
        match cmd:
            case ListChats():
                return {"chats": [c.model_dump(mode="json") for c in self.chats]}
            case SendText():
                if self.send_error is not None:
                    raise self.send_error
                if self.follow_up is not None and self.server is not None:
                    ev, srv = self.follow_up, self.server
                    asyncio.get_running_loop().call_later(0.1, srv.broadcast, ev)
                res: dict[str, Any] = {"state": self.send_state, "wa_id": "w1"}
                if self.send_err_text:
                    res["error"] = self.send_err_text
                return res
            case _:
                return {}

    def sent(self) -> list[SendText]:
        return [c for c in self.cmds if isinstance(c, SendText)]


async def no_backfill(since: int) -> tuple[list[MessageNew], bool]:
    return [], False


@pytest.fixture
def daemon() -> FakeDaemon:
    return FakeDaemon()


@pytest.fixture
async def server(short_path: Path, daemon: FakeDaemon) -> AsyncIterator[Server]:
    srv = Server(short_path / "s.sock", daemon, no_backfill)
    daemon.server = srv
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture
def sock(short_path: Path) -> Path:
    return short_path / "s.sock"


# ---- send ------------------------------------------------------------------------------------


async def test_send_ok(server: Server, sock: Path, daemon: FakeDaemon) -> None:
    err = io.StringIO()
    assert await script.send("juan", "hola", socket=sock, err=err) == script.EXIT_OK
    assert [(c.chat_jid, c.text) for c in daemon.sent()] == [(JUAN, "hola")]
    assert err.getvalue() == ""


async def test_send_ambiguous_chat(server: Server, sock: Path, daemon: FakeDaemon) -> None:
    daemon.chats = [chat(JUAN, "Carlos A"), chat(ANA, "Carlos B")]
    err = io.StringIO()
    assert await script.send("carlos", "hola", socket=sock, err=err) == script.EXIT_NO_CHAT
    assert "ambiguo" in err.getvalue() and "Carlos A" in err.getvalue()
    assert daemon.sent() == []


async def test_send_unknown_chat(server: Server, sock: Path, daemon: FakeDaemon) -> None:
    err = io.StringIO()
    assert await script.send("zzzzzz", "hola", socket=sock, err=err) == script.EXIT_NO_CHAT
    assert "no encuentro el chat" in err.getvalue()
    assert daemon.sent() == []


async def test_send_daemon_down(short_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(script, "CONNECT_TIMEOUT", 0.2)
    err = io.StringIO()
    code = await script.send("juan", "hola", socket=short_path / "no.sock", err=err)
    assert code == script.EXIT_UNREACHABLE
    assert "wspd" in err.getvalue()


async def test_send_failed_state(server: Server, sock: Path, daemon: FakeDaemon) -> None:
    daemon.send_state = "failed"
    daemon.send_err_text = "sin red\x1b[2J"
    err = io.StringIO()
    assert await script.send("juan", "hola", socket=sock, err=err) == script.EXIT_FAILED
    assert "envío fallido: sin red" in err.getvalue()
    assert "\x1b" not in err.getvalue()


async def test_send_remote_error(server: Server, sock: Path, daemon: FakeDaemon) -> None:
    daemon.send_error = CommandError("rate_limited", "demasiados envíos")
    err = io.StringIO()
    assert await script.send("juan", "hola", socket=sock, err=err) == script.EXIT_FAILED
    assert "rate_limited: demasiados envíos" in err.getvalue()


@pytest.mark.parametrize("text", ["", "   ", "\n"])
async def test_send_empty_text(text: str, server: Server, sock: Path, daemon: FakeDaemon) -> None:
    err = io.StringIO()
    assert await script.send("juan", text, socket=sock, err=err) == script.EXIT_FAILED
    assert "vacío" in err.getvalue()
    assert daemon.cmds == []


async def test_send_too_long(server: Server, sock: Path, daemon: FakeDaemon) -> None:
    err = io.StringIO()
    code = await script.send("juan", "x" * (script.MAX_STDIN + 1), socket=sock, err=err)
    assert code == script.EXIT_FAILED and "largo" in err.getvalue()
    assert daemon.cmds == []


def test_read_text_stdin_and_literal() -> None:
    assert script.read_text("-", io.StringIO("hola\nmundo\n")) == "hola\nmundo"
    assert script.read_text("-", io.StringIO("")) == ""
    assert script.read_text("hola", io.StringIO("ignorado")) == "hola"
    assert len(script.read_text("-", io.StringIO("x" * (script.MAX_STDIN + 10)))) == (
        script.MAX_STDIN + 1
    )


async def test_send_text_from_stdin(server: Server, sock: Path, daemon: FakeDaemon) -> None:
    text = script.read_text("-", io.StringIO("desde stdin\n"))
    assert await script.send("juan", text, socket=sock, err=io.StringIO()) == script.EXIT_OK
    assert daemon.sent()[0].text == "desde stdin"


@pytest.mark.parametrize(
    ("status", "error", "code"),
    [("delivered", None, script.EXIT_OK), ("read", None, script.EXIT_OK),
     ("failed", "sin red", script.EXIT_FAILED)],
)  # fmt: skip
async def test_send_wait_delivered(
    status: str, error: str | None, code: int, server: Server, sock: Path, daemon: FakeDaemon
) -> None:
    daemon.follow_up = MessageStatus(chat_jid=JUAN, wa_id="w1", status=status, error=error)  # type: ignore[arg-type]
    err = io.StringIO()
    res = await script.send("juan", "hola", wait_delivered=True, timeout=2.0, socket=sock, err=err)
    assert res == code
    if code:
        assert "sin red" in err.getvalue()


async def test_send_wait_delivered_ignores_other_chats(
    server: Server, sock: Path, daemon: FakeDaemon
) -> None:
    daemon.follow_up = MessageStatus(chat_jid=ANA, wa_id="w9", status="delivered")
    err = io.StringIO()
    res = await script.send("juan", "hola", wait_delivered=True, timeout=0.4, socket=sock, err=err)
    assert res == script.EXIT_TIMEOUT


async def test_send_wait_delivered_timeout(server: Server, sock: Path) -> None:
    err = io.StringIO()
    res = await script.send("juan", "hola", wait_delivered=True, timeout=0.2, socket=sock, err=err)
    assert res == script.EXIT_TIMEOUT
    assert "sin confirmación" in err.getvalue()


# ---- tail ------------------------------------------------------------------------------------


async def start_tail(
    server: Server, sock: Path, daemon: FakeDaemon, chat_arg: str | None, as_json: bool
) -> tuple[asyncio.Task[int], io.StringIO, io.StringIO]:
    out, err = io.StringIO(), io.StringIO()
    task = asyncio.create_task(
        script.tail(chat_arg, as_json=as_json, pal=Palette(False), out=out, err=err, socket=sock)
    )
    await until(lambda: any(isinstance(c, ListChats) for c in daemon.cmds))
    await asyncio.sleep(0.1)
    return task, out, err


def msg(wa_id: str, chat_jid: str, body: str) -> MessageNew:
    return MessageNew(chat_jid=chat_jid, wa_id=wa_id, ts=T0, body=body)


async def test_tail_text_format(server: Server, sock: Path, daemon: FakeDaemon) -> None:
    from wsp.cli import render

    task, out, _ = await start_tail(server, sock, daemon, None, False)
    server.broadcast(msg("m1", JUAN, "hola"))
    server.broadcast(msg("m2", ANA, "buenas"))
    await until(lambda: "buenas" in out.getvalue())
    task.cancel()
    lines = out.getvalue().splitlines()
    tz_time = render.format_time(T0, None)
    assert f"[{tz_time}] Juan › hola" in lines
    assert f"[{tz_time}] Ana › buenas" in lines


async def test_tail_json_one_event_per_line(server: Server, sock: Path, daemon: FakeDaemon) -> None:
    task, out, _ = await start_tail(server, sock, daemon, None, True)
    server.broadcast(msg("m1", JUAN, "hola ñ"))
    server.broadcast(MessageStatus(chat_jid=ANA, wa_id="m2", status="delivered"))
    await until(lambda: len(out.getvalue().splitlines()) >= 2)
    task.cancel()
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert rows[0]["type"] == "message.new"
    assert rows[0]["data"]["body"] == "hola ñ"
    assert "request_id" not in rows[0]
    assert rows[1]["type"] == "message.status"
    assert rows[1]["data"]["status"] == "delivered"


async def test_tail_json_filters_by_chat(server: Server, sock: Path, daemon: FakeDaemon) -> None:
    task, out, _ = await start_tail(server, sock, daemon, "ana", True)
    server.broadcast(msg("m1", JUAN, "ruido"))
    server.broadcast(msg("m2", ANA, "señal"))
    await until(lambda: "m2" in out.getvalue())
    task.cancel()
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["data"]["wa_id"] for r in rows] == ["m2"]
    assert rows[0]["data"]["body"] == "señal"
    assert "ñ" not in out.getvalue()  # ASCII puro: ningún control Unicode llega a la terminal


async def test_tail_unknown_chat(server: Server, sock: Path) -> None:
    err = io.StringIO()
    code = await script.tail(
        "zzzzzz", as_json=False, pal=Palette(False), out=io.StringIO(), err=err, socket=sock
    )
    assert code == script.EXIT_NO_CHAT
    assert "no encuentro el chat" in err.getvalue()


async def test_tail_daemon_down(short_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(script, "CONNECT_TIMEOUT", 0.2)
    code = await script.tail(
        None,
        as_json=True,
        pal=Palette(False),
        out=io.StringIO(),
        err=io.StringIO(),
        socket=short_path / "no.sock",
    )
    assert code == script.EXIT_UNREACHABLE
