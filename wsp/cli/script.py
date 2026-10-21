"""Modo script: `wsp send` y `wsp tail` (sin prompt; aptos para pipes y `jq`)."""

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Final, TextIO

from pydantic import ValidationError

from wsp import paths
from wsp.cli import commands, render
from wsp.cli.render import Palette
from wsp.cli.repl import CONNECT_TIMEOUT, MAX_LIST, Repl
from wsp.protocol.client import Client, ConnectionLost, RemoteError, RequestTimeout
from wsp.protocol.models import (
    ChatUpdated,
    ListChats,
    MessageStatus,
    SendText,
    to_envelope,
)

EXIT_OK: Final = 0
EXIT_FAILED: Final = 1  # el envío falló o argumentos inválidos
EXIT_UNREACHABLE: Final = 2
EXIT_NO_CHAT: Final = 4  # chat inexistente o ambiguo
EXIT_TIMEOUT: Final = 5  # --wait-delivered agotó el tiempo
DELIVERY_TIMEOUT: Final = 30.0
MAX_STDIN: Final = 64 * 1024


def _fail(err: TextIO, text: str) -> None:
    err.write(f"wsp: {render.one_line(text)}\n")
    err.flush()


async def _connect(client: Client, err: TextIO) -> bool:
    try:
        await client.wait_connected(CONNECT_TIMEOUT)
    except ConnectionLost:
        _fail(err, "el daemon (wspd) no responde; revisa `wsp daemon status`")
        return False
    return True


async def _chats(client: Client) -> dict[str, ChatUpdated]:
    res = await client.request(ListChats(limit=MAX_LIST))
    return {c.jid: c for c in (ChatUpdated.model_validate(x) for x in res.get("chats", []))}


def _pick(query: str, chats: dict[str, ChatUpdated], err: TextIO) -> str | None:
    """jid del chat para `query`; con duda o sin coincidencia, explica y devuelve None."""
    match commands.resolve_chat(query, chats, []):
        case commands.Found(jid):
            return jid
        case commands.Ambiguous(jids):
            names = ", ".join(render.chat_name(chats[j]) for j in jids)
            _fail(err, f"chat ambiguo «{render.truncate(query, 30)}»; candidatos: {names}")
        case _:
            _fail(err, f"no encuentro el chat «{render.truncate(query, 30)}»")
    return None


def read_text(arg: str, stdin: TextIO) -> str:
    """`-` lee de stdin (sin el salto de línea final); si no, el argumento tal cual."""
    if arg != "-":
        return arg
    return stdin.read(MAX_STDIN + 1).rstrip("\r\n")


async def _wait_delivered(client: Client, chat_jid: str, timeout: float) -> str | None:  # noqa: ASYNC109
    """None si llegó (entregado/leído); si no, el motivo. Se correlaciona por chat."""
    try:
        async with asyncio.timeout(timeout):
            async for ev in client.events():
                if not isinstance(ev, MessageStatus) or ev.chat_jid != chat_jid:
                    continue
                if ev.status in ("delivered", "read"):
                    return None
                if ev.status == "failed":
                    return ev.error or "envío fallido"
    except TimeoutError:
        return "timeout"
    return "conexión cerrada"


async def send(
    chat: str,
    text: str,
    *,
    wait_delivered: bool = False,
    timeout: float = DELIVERY_TIMEOUT,  # noqa: ASYNC109
    socket: Path | None = None,
    err: TextIO | None = None,
) -> int:
    """0 si el mensaje queda enviado; ≠0 y motivo en stderr si no."""
    err = err or sys.stderr
    if not text.strip():
        _fail(err, "mensaje vacío")
        return EXIT_FAILED
    if len(text) > MAX_STDIN:
        _fail(err, "mensaje demasiado largo")
        return EXIT_FAILED
    async with Client(socket or paths.socket_path(), kind="cli") as client:
        if not await _connect(client, err):
            return EXIT_UNREACHABLE
        try:
            jid = _pick(chat, await _chats(client), err)
            if jid is None:
                return EXIT_NO_CHAT
            res = await client.request(SendText(chat_jid=jid, text=text))
        except RemoteError as e:
            _fail(err, f"{render.truncate(e.code, 64)}: {render.truncate(e.message, 200)}")
            return EXIT_FAILED
        except (RequestTimeout, ConnectionLost, ValidationError):
            _fail(err, "sin respuesta válida del daemon")
            return EXIT_FAILED
        if res.get("state") == "failed":
            _fail(err, f"envío fallido: {render.one_line(str(res.get('error', '')))}")
            return EXIT_FAILED
        if wait_delivered:
            problem = await _wait_delivered(client, jid, timeout)
            if problem == "timeout":
                _fail(err, "enviado, pero sin confirmación de entrega a tiempo")
                return EXIT_TIMEOUT
            if problem is not None:
                _fail(err, problem)
                return EXIT_FAILED
    return EXIT_OK


async def tail(
    chat: str | None,
    *,
    as_json: bool,
    pal: Palette,
    out: TextIO | None = None,
    err: TextIO | None = None,
    socket: Path | None = None,
) -> int:
    """Imprime eventos en vivo hasta Ctrl+C o pipe cerrado. `--json`: un evento por línea."""
    out, err = out or sys.stdout, err or sys.stderr
    async with Client(socket or paths.socket_path(), kind="tail") as client:
        if not await _connect(client, err):
            return EXIT_UNREACHABLE
        repl = Repl(client, out, pal)
        try:
            await repl.preload()
            jid: str | None = None
            if chat is not None:
                jid = _pick(chat, repl.chats, err)
                if jid is None:
                    return EXIT_NO_CHAT
            async for ev in client.events():
                where = getattr(ev, "chat_jid", None) or getattr(ev, "jid", None)
                if jid is not None and where is not None and where != jid:
                    continue
                if as_json:
                    env = to_envelope(ev).model_dump(mode="json", exclude={"request_id"})
                    out.write(json.dumps(env, ensure_ascii=False, separators=(",", ":")) + "\n")
                    out.flush()
                else:
                    repl.handle(ev)
        except BrokenPipeError:
            with contextlib.suppress(OSError, ValueError):  # evita el error al cerrar
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            return EXIT_OK
        except (RemoteError, RequestTimeout, ConnectionLost, ValidationError):
            _fail(err, "sin respuesta válida del daemon")
            return EXIT_FAILED
    return EXIT_OK
