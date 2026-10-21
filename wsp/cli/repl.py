"""REPL en vivo de `wsp`: línea de entrada con eventos entrantes impresos encima del prompt."""

import asyncio
import contextlib
import os
import sys
from collections import OrderedDict
from collections.abc import Callable
from datetime import tzinfo
from pathlib import Path
from typing import Any, Final, TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from pydantic import ValidationError
from rapidfuzz import fuzz, process

from wsp import paths
from wsp.cli import daemon_cmd, render
from wsp.cli.render import Palette
from wsp.protocol.client import Client, ConnectionLost, RemoteError, RequestTimeout
from wsp.protocol.models import (
    AccountState,
    BackfillEnd,
    BackfillStart,
    ChatUpdated,
    ErrorEvent,
    Event,
    History,
    ListChats,
    MarkRead,
    MessageDeleted,
    MessageEdited,
    MessageNew,
    MessageStatus,
    Reaction,
    SendText,
)

EXIT_OK: Final = 0
EXIT_UNREACHABLE: Final = 2
EXIT_UNLINKED: Final = 3
CONNECT_TIMEOUT: Final = 3.0
ACCOUNT_WAIT: Final = 1.0
DEFAULT_CHATS: Final = 30
DEFAULT_HISTORY: Final = 20
MAX_LIST: Final = 200
SEEN_MAX: Final = 4096
PROMPT_TITLE_MAX: Final = 24
_UNLINKED: Final = frozenset({"pairing", "logged_out"})
_FUZZY_CUTOFF: Final = 60

Ask = Callable[[str], str]


class Repl:
    """Estado y manejadores del REPL; todo lo que imprime pasa por `out` (stdout parcheado)."""

    def __init__(
        self,
        client: Client,
        out: TextIO | None,
        pal: Palette,
        *,
        tz: tzinfo | None = None,
    ) -> None:
        self._client = client
        self._out = out
        self._pal = pal
        self._tz = tz
        self.chats: dict[str, ChatUpdated] = {}
        self.listing: list[str] = []  # jids de la última salida de /chats (índice = n - 1)
        self.open_jid: str | None = None
        self.account: AccountState | None = None
        self.account_seen = asyncio.Event()
        self._msgs: OrderedDict[tuple[str, str], MessageNew] = OrderedDict()
        self._printed: set[tuple[str, str]] = set()
        self._last_day: tuple[int, int, int] | None = None
        self._backfill: list[MessageNew] | None = None
        self._was_down = False

    # ---- salida -------------------------------------------------------------------------

    def _emit(self, *lines: str) -> None:
        out = self._out or sys.stdout  # stdout se resuelve al escribir: patch_stdout lo reemplaza
        out.write("\n".join(lines) + "\n")
        out.flush()

    def note(self, text: str) -> None:
        self._emit(self._pal.dim(f"  {text}"))

    def error(self, text: str) -> None:
        self._emit(self._pal.paint(f"  ✗ {text}", 31))

    # ---- nombres ------------------------------------------------------------------------

    def name_of(self, jid: str) -> str:
        chat = self.chats.get(jid)
        if chat is not None and chat.title:
            return render.truncate(chat.title, 40)
        return render.short_jid(jid)

    def prompt(self) -> str:
        if self.open_jid is None:
            return f"{render.BULLET} "
        title = render.truncate(self.name_of(self.open_jid), PROMPT_TITLE_MAX)
        return f"{title} {render.BULLET} "

    # ---- eventos ------------------------------------------------------------------------

    def handle(self, ev: Event) -> None:
        match ev:
            case MessageNew():
                self._on_message(ev)
            case MessageStatus():
                self._on_status(ev)
            case MessageEdited():
                self._on_changed(ev.chat_jid, ev.wa_id, body=ev.body, edited_at=ev.edited_at)
            case MessageDeleted():
                self._on_changed(ev.chat_jid, ev.wa_id, deleted_at=ev.deleted_at)
            case Reaction():
                target = self._msgs.get((ev.chat_jid, ev.wa_id))
                self._emit(render.render_reaction(ev, target, self.name_of, self._pal))
            case ChatUpdated():
                self.chats[ev.jid] = ev
            case AccountState():
                self._on_account(ev)
            case BackfillStart():
                self._backfill = []
            case BackfillEnd():
                self._on_backfill_end(ev)
            case ErrorEvent():
                self.error(f"{render.one_line(ev.code)}: {render.one_line(ev.message)}")
            case _:
                pass  # presencia: sin salida

    def _remember(self, m: MessageNew) -> None:
        key = (m.chat_jid, m.wa_id)
        self._msgs[key] = m
        self._msgs.move_to_end(key)
        if len(self._msgs) > SEEN_MAX:
            old, _ = self._msgs.popitem(last=False)
            self._printed.discard(old)

    def _on_message(self, m: MessageNew) -> None:
        if self._backfill is not None:
            self._backfill.append(m)
            return
        known = (m.chat_jid, m.wa_id) in self._printed
        self._remember(m)
        if not known:  # una actualización de un mensaje ya impreso no se reimprime
            self._print_message(m)

    def _print_message(self, m: MessageNew) -> None:
        key = (m.chat_jid, m.wa_id)
        self._printed.add(key)
        lines: list[str] = []
        day = render.day_key(m.ts, self._tz)
        if day != self._last_day:
            self._last_day = day
            lines.append(render.date_separator(m.ts, self._pal, self._tz))
        chat = self.chats.get(m.chat_jid)
        group = self.name_of(m.chat_jid) if chat is not None and chat.kind == "group" else None
        quoted = None if m.quoted_wa_id is None else self._msgs.get((m.chat_jid, m.quoted_wa_id))
        lines += render.render_message(
            m, name_of=self.name_of, pal=self._pal, group_title=group, quoted=quoted, tz=self._tz
        )
        self._emit(*lines)

    def _on_status(self, s: MessageStatus) -> None:
        key = (s.chat_jid, s.wa_id)
        prev = self._msgs.get(key)
        if prev is not None:
            self._msgs[key] = prev.model_copy(update={"status": s.status, "error": s.error})
        if s.chat_jid == self.open_jid:
            self._emit(render.render_status_line(s, self._pal))
        elif s.status == "failed":  # los fallos se avisan aunque el chat no esté abierto
            self._emit(render.render_status_line(s, self._pal, self.name_of(s.chat_jid)))

    def _on_changed(self, chat_jid: str, wa_id: str, **changes: Any) -> None:
        """Edición/borrado: reimprime el mensaje con su marca (`(editado)`/eliminado)."""
        prev = self._msgs.get((chat_jid, wa_id))
        if prev is None:
            return  # no estaba en pantalla: se verá correcto al abrir el chat
        try:
            m = prev.model_copy(update=changes)
        except ValidationError:  # pragma: no cover - model_copy no valida
            return
        self._remember(m)
        self._print_message(m)

    def _on_account(self, a: AccountState) -> None:
        self.account = a
        self.account_seen.set()
        if a.state in ("disconnected", "logged_out"):
            self._was_down = True
            reason = f" ({render.one_line(a.reason)})" if a.reason else ""
            self._emit(render.separator(f"cuenta {a.state}{reason}", self._pal))
        elif a.state == "connected" and self._was_down:
            self._was_down = False
            self._emit(render.separator("cuenta conectada", self._pal))

    def _on_backfill_end(self, end: BackfillEnd) -> None:
        batch, self._backfill = self._backfill or [], None
        self._emit(render.reconnect_header(end.count, self._pal))
        for m in batch:
            key = (m.chat_jid, m.wa_id)
            known = key in self._printed
            self._remember(m)
            if not known:
                self._print_message(m)
        if end.count:
            self._emit(render.separator("fin del relleno", self._pal))

    # ---- comandos de usuario ------------------------------------------------------------

    async def handle_line(self, line: str) -> bool:
        """Procesa una línea. False ⇒ salir."""
        text = line.strip()
        if not text:
            return True
        if not text.startswith("/"):
            await self._send(text)
            return True
        cmd, _, arg = text[1:].partition(" ")
        arg = arg.strip()
        match cmd.lower():
            case "quit" | "exit" | "q":
                return False
            case "help" | "h" | "?":
                self._emit(*render.HELP_TEXT)
            case "chats":
                await self._cmd_chats(arg)
            case "open":
                await self._cmd_open(arg)
            case "back":
                self.open_jid = None
            case "history":
                await self._cmd_history(arg)
            case "r" | "react" | "search" | "mute":
                self.note(f"/{cmd.lower()}: aún no disponible.")
            case _:
                self.error(f"comando desconocido: /{render.one_line(cmd)} (/help)")
        return True

    async def _call(self, cmd: Any) -> dict[str, Any] | None:
        """Request al daemon; imprime un error claro (sin traceback) y devuelve None."""
        try:
            return dict(await self._client.request(cmd))
        except RemoteError as e:
            self.error(f"{render.one_line(e.code)}: {render.one_line(e.message)}")
        except (ConnectionLost, RequestTimeout) as e:
            self.error(f"sin respuesta del daemon ({type(e).__name__}); revisa `wsp daemon status`")
        return None

    async def _send(self, text: str) -> None:
        if self.open_jid is None:
            self._emit(*render.HELP_NO_CHAT)
            return
        res = await self._call(SendText(chat_jid=self.open_jid, text=text))
        if res is not None and res.get("state") == "failed":
            self.error(f"envío fallido: {render.one_line(str(res.get('error', '')))}")

    async def _load_chats(self, limit: int) -> list[ChatUpdated]:
        res = await self._call(ListChats(limit=limit))
        if res is None:
            return []
        try:
            chats = [ChatUpdated.model_validate(c) for c in res.get("chats", [])]
        except ValidationError:
            self.error("respuesta inválida del daemon")
            return []
        for c in chats:
            self.chats[c.jid] = c
        return chats

    async def _cmd_chats(self, arg: str) -> None:
        n = _parse_count(arg, DEFAULT_CHATS)
        if n is None:
            self.error("uso: /chats [n]")
            return
        chats = await self._load_chats(n)
        self.listing = [c.jid for c in chats]
        if not chats:
            self.note("sin chats todavía.")
            return
        self._emit(*render.render_chat_list(chats, self._pal))

    async def _cmd_open(self, arg: str) -> None:
        if not arg:
            self.error("uso: /open <n|nombre>")
            return
        jid = await self._resolve(arg)
        if jid is None:
            self.error(f"no encuentro el chat «{render.truncate(arg, 30)}» (/chats)")
            return
        self.open_jid = jid
        await self._show_history(jid, DEFAULT_HISTORY)
        await self._call(MarkRead(chat_jid=jid))

    async def _resolve(self, arg: str) -> str | None:
        if arg.isdigit():
            i = int(arg)
            if not self.listing:
                await self._cmd_chats("")
            return self.listing[i - 1] if 1 <= i <= len(self.listing) else None
        if not self.chats:
            await self._load_chats(MAX_LIST)
        titles = {jid: c.title for jid, c in self.chats.items() if c.title}
        best = process.extractOne(
            arg, titles, scorer=fuzz.WRatio, score_cutoff=_FUZZY_CUTOFF, processor=str.lower
        )
        return None if best is None else str(best[2])

    async def _cmd_history(self, arg: str) -> None:
        if self.open_jid is None:
            self._emit(*render.HELP_NO_CHAT)
            return
        n = _parse_count(arg, DEFAULT_HISTORY)
        if n is None:
            self.error("uso: /history [n]")
            return
        await self._show_history(self.open_jid, n)

    async def _show_history(self, jid: str, n: int) -> None:
        res = await self._call(History(chat_jid=jid, n=min(n, 1000)))
        if res is None:
            return
        try:
            rows = [MessageNew.model_validate(m) for m in res.get("messages", [])]
        except ValidationError:
            self.error("respuesta inválida del daemon")
            return
        self._last_day = None  # el historial abre con su fecha
        for m in rows:
            self._remember(m)
            self._print_message(m)
        if not rows:
            self.note("sin mensajes todavía.")


def _parse_count(arg: str, default: int) -> int | None:
    if not arg:
        return default
    return int(arg) if arg.isdigit() and 0 < int(arg) <= MAX_LIST else None


async def _pump(client: Client, repl: Repl) -> None:
    async for ev in client.events():
        repl.handle(ev)


def _history_file() -> FileHistory:
    """Historial de entrada en `~/.local/state/wsp/history` con permisos 600."""
    path = paths.state_dir() / "history"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.close(fd)
    path.chmod(0o600)
    return FileHistory(str(path))


async def run_loop(repl: Repl, session: PromptSession[str]) -> int:
    """Bucle de entrada. Ctrl+C limpia la línea; Ctrl+D (EOF) o /quit salen."""
    while True:
        try:
            line = await session.prompt_async(repl.prompt)
        except KeyboardInterrupt:
            continue
        except EOFError:
            return EXIT_OK
        if not await repl.handle_line(line):
            return EXIT_OK


def offer_daemon_help(ask: Ask, out: TextIO) -> None:
    """El daemon no responde: ofrece `wsp daemon install` o `wsp daemon status`."""
    out.write("wsp: el daemon (wspd) no responde.\n")
    out.write("  [i] ejecutar `wsp daemon install`\n  [s] ejecutar `wsp daemon status`\n")
    try:
        choice = ask("¿Qué hacemos? [i/s/Enter=salir] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if choice == "i":
        daemon_cmd.install(out=out)
    elif choice == "s":
        daemon_cmd.status(out=out)


async def run(
    *,
    pal: Palette,
    session: PromptSession[str] | None = None,
    out: TextIO | None = None,
    ask: Ask = input,
    tz: tzinfo | None = None,
    socket: Path | None = None,
) -> int:
    """Abre el REPL. Códigos: 0 ok · 2 daemon sin respuesta · 3 cuenta sin vincular."""
    path = socket or paths.socket_path()
    async with Client(path, kind="cli") as client:
        try:
            await client.wait_connected(CONNECT_TIMEOUT)
        except ConnectionLost:
            offer_daemon_help(ask, out or sys.stdout)
            return EXIT_UNREACHABLE
        repl = Repl(client, out, pal, tz=tz)
        pump = asyncio.create_task(_pump(client, repl), name="wsp-pump")
        try:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(repl.account_seen.wait(), ACCOUNT_WAIT)
            if repl.account is not None and repl.account.state in _UNLINKED:
                (out or sys.stdout).write("wsp: la cuenta no está vinculada. Ejecuta `wsp pair`.\n")
                return EXIT_UNLINKED
            if session is None:
                session = PromptSession(history=_history_file())
            with patch_stdout(raw=True):
                repl.note("conectado. /help para ver comandos.")
                return await run_loop(repl, session)
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
