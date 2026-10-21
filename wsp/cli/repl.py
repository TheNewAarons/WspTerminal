"""REPL en vivo de `wsp`: línea de entrada con eventos entrantes impresos encima del prompt."""

import asyncio
import contextlib
import os
import sys
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from datetime import tzinfo
from pathlib import Path
from typing import Any, Final, TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from pydantic import BaseModel, ValidationError

from wsp import paths
from wsp.cli import commands, daemon_cmd, notify, render
from wsp.cli.completer import WspCompleter
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
    Mute,
    React,
    Reaction,
    Search,
    SendReply,
    SendText,
)

EXIT_OK: Final = 0
EXIT_UNREACHABLE: Final = 2
EXIT_UNLINKED: Final = 3
CONNECT_TIMEOUT: Final = 3.0
ACCOUNT_WAIT: Final = 1.0
MAX_LIST: Final = 200
SEEN_MAX: Final = 4096
PROMPT_TITLE_MAX: Final = 24
_UNLINKED: Final = frozenset({"pairing", "logged_out"})

Ask = Callable[[str], str]
Notifier = Callable[[str, str], Awaitable[object]]
_BAD_REPLY: Final = (ValidationError, TypeError, ValueError, AttributeError)


def remote_error_text(e: RemoteError) -> str:
    """`código: mensaje` acotado, para mostrar errores del daemon sin volcar contenido."""
    return f"{render.truncate(e.code, 64)}: {render.truncate(e.message, 200)}"


def send_failure(res: Mapping[str, Any] | None) -> str | None:
    """Texto del fallo si la respuesta de un envío indica `failed`; si no, None."""
    if res is not None and res.get("state") == "failed":
        return f"envío fallido: {render.one_line(str(res.get('error', '')))}"
    return None


def parse_rows[M: BaseModel](model: type[M], res: Mapping[str, Any], key: str) -> list[M]:
    """Valida `res[key]` como lista de `model`; ValidationError/TypeError si el daemon miente."""
    return [model.model_validate(x) for x in res.get(key, [])]


class Repl:
    """Estado y manejadores del REPL; todo lo que imprime pasa por `out` (stdout parcheado)."""

    def __init__(
        self,
        client: Client,
        out: TextIO | None,
        pal: Palette,
        *,
        tz: tzinfo | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self._client = client
        self._notifier = notifier
        self._tasks: set[asyncio.Task[object]] = set()
        self._out = out
        self._pal = pal
        self._tz = tz
        self.chats: dict[str, ChatUpdated] = {}
        self.listing: list[str] = []  # jids de la última salida de /chats (índice = n - 1)
        self.open_jid: str | None = None
        self.view: list[MessageNew] = []  # mensajes numerados del chat abierto (n = índice + 1)
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
                self.error(f"{render.truncate(ev.code, 64)}: {render.truncate(ev.message, 200)}")
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
        if self._ingest(m):
            self._maybe_notify(m)

    def _ingest(self, m: MessageNew) -> bool:
        """Recuerda `m` y lo imprime si es nuevo (una actualización no se reimprime)."""
        known = (m.chat_jid, m.wa_id) in self._printed
        self._remember(m)
        if not known:
            self._print_message(m)
        return not known

    def _maybe_notify(self, m: MessageNew) -> None:
        """Notifica mensajes ajenos de chats no abiertos ni silenciados."""
        if self._notifier is None or m.from_me or m.deleted_at is not None:
            return
        if m.chat_jid == self.open_jid:
            return
        chat = self.chats.get(m.chat_jid)
        if chat is not None and chat.muted_until > time.time():
            return
        who = self.name_of(m.chat_jid)
        body = render.one_line(m.body or f"[{m.kind}]")
        if chat is not None and chat.kind == "group":
            body = f"{self.name_of(m.sender_jid or m.chat_jid)}: {body}"
        task = asyncio.ensure_future(self._notifier(who, body))
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[object]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()  # una notificación fallida no debe dejar "exception never retrieved"

    async def aclose(self) -> None:
        """Cancela y espera las notificaciones pendientes (al salir del REPL)."""
        pending = list(self._tasks)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

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
            m,
            name_of=self.name_of,
            pal=self._pal,
            group_title=group,
            quoted=quoted,
            tz=self._tz,
            num=self._number(m),
        )
        self._emit(*lines)

    def _number(self, m: MessageNew) -> int | None:
        """Número del mensaje en la vista del chat abierto (se asigna al verlo por 1.ª vez)."""
        if m.chat_jid != self.open_jid:
            return None
        for i, v in enumerate(self.view):
            if v.wa_id == m.wa_id:
                self.view[i] = m
                return i + 1
        self.view.append(m)
        return len(self.view)

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
            self._ingest(m)
        if end.count:
            self._emit(render.separator("fin del relleno", self._pal))

    # ---- comandos de usuario ------------------------------------------------------------

    async def handle_line(self, line: str) -> bool:
        """Procesa una línea. False ⇒ salir."""
        cmd = commands.parse(line)
        match cmd:
            case commands.Empty():
                pass
            case commands.Quit():
                return False
            case commands.Say():
                await self._send(cmd.text)
            case commands.Invalid():
                self.error(cmd.message)
            case commands.Help():
                self._emit(*render.help_lines())
            case commands.ShowChats():
                await self._cmd_chats(cmd.n)
            case commands.Open():
                await self._cmd_open(cmd.query)
            case commands.Back():
                self._cmd_back()
            case commands.ShowHistory():
                await self._cmd_history(cmd.n)
            case commands.Reply():
                await self._cmd_reply(cmd)
            case commands.ReactTo():
                await self._cmd_react(cmd)
            case commands.SearchFor():
                await self._cmd_search(cmd.query)
            case commands.MuteChat():
                await self._cmd_mute(cmd)
        return True

    def _cmd_back(self) -> None:
        self.open_jid = None
        self.view = []

    async def _call(self, cmd: Any) -> dict[str, Any] | None:
        """Request al daemon; imprime un error claro (sin traceback) y devuelve None."""
        try:
            return dict(await self._client.request(cmd))
        except RemoteError as e:
            self.error(remote_error_text(e))
        except (ConnectionLost, RequestTimeout) as e:
            self.error(f"sin respuesta del daemon ({type(e).__name__}); revisa `wsp daemon status`")
        return None

    def _report_send(self, res: dict[str, Any] | None) -> None:
        if (text := send_failure(res)) is not None:
            self.error(text)

    async def _send(self, text: str) -> None:
        if self.open_jid is None:
            self._emit(*render.HELP_NO_CHAT)
            return
        self._report_send(await self._call(SendText(chat_jid=self.open_jid, text=text)))

    def _target(self, n: int) -> MessageNew | None:
        """Mensaje número `n` de la vista actual; avisa si no hay chat o n está fuera de rango."""
        if self.open_jid is None:
            self._emit(*render.HELP_NO_CHAT)
            return None
        if not 1 <= n <= len(self.view):
            self.error(f"no hay mensaje {n} en la vista (1-{len(self.view)})")
            return None
        return self.view[n - 1]

    async def _cmd_reply(self, cmd: commands.Reply) -> None:
        target = self._target(cmd.n)
        if target is not None:
            self._report_send(
                await self._call(
                    SendReply(chat_jid=target.chat_jid, quoted_wa_id=target.wa_id, text=cmd.text)
                )
            )

    async def _cmd_react(self, cmd: commands.ReactTo) -> None:
        target = self._target(cmd.n)
        if target is not None:
            await self._call(React(chat_jid=target.chat_jid, wa_id=target.wa_id, emoji=cmd.emoji))

    async def _load_chats(self, limit: int) -> list[ChatUpdated]:
        res = await self._call(ListChats(limit=limit))
        if res is None:
            return []
        try:
            chats = parse_rows(ChatUpdated, res, "chats")
        except _BAD_REPLY:
            self.error("respuesta inválida del daemon")
            return []
        for c in chats:
            self.chats[c.jid] = c
        return chats

    async def preload(self) -> None:
        """Carga los chats para nombres y autocompletado (sin imprimir nada)."""
        await self._load_chats(MAX_LIST)

    async def _cmd_chats(self, n: int) -> None:
        chats = await self._load_chats(n)
        self.listing = [c.jid for c in chats]
        if not chats:
            self.note("sin chats todavía.")
            return
        self._emit(*render.render_chat_list(chats, self._pal))

    async def _resolve(self, query: str) -> str | None:
        """Chat para `query` (número o nombre). Imprime el motivo si no hay uno claro."""
        if not self.chats:
            await self._load_chats(MAX_LIST)
        if query.strip().isdigit() and not self.listing:
            await self._cmd_chats(commands.DEFAULT_CHATS)
        res = commands.resolve_chat(query, self.chats, self.listing)
        if isinstance(res, commands.Found):
            return res.jid
        self._explain_unresolved(query, res)
        return None

    def _explain_unresolved(self, query: str, res: commands.Resolution) -> None:
        match res:
            case commands.Ambiguous(jids):
                self.listing = list(jids)
                self.note(f"«{render.truncate(query, 30)}» es ambiguo; elige con /open <n>:")
                self._emit(*render.render_chat_list([self.chats[j] for j in jids], self._pal))
            case commands.OutOfRange(n, size):
                self.error(f"no hay chat {n} en la lista" + (f" (1-{size})" if size else ""))
            case _:
                self.error(f"no encuentro el chat «{render.truncate(query, 30)}» (/chats)")

    async def _cmd_open(self, query: str) -> None:
        jid = await self._resolve(query)
        if jid is None:
            return
        self.open_jid = jid
        await self._show_history(jid, commands.DEFAULT_HISTORY)
        await self._call(MarkRead(chat_jid=jid))

    async def _cmd_history(self, n: int) -> None:
        if self.open_jid is None:
            self._emit(*render.HELP_NO_CHAT)
            return
        await self._show_history(self.open_jid, n)

    async def _show_history(self, jid: str, n: int) -> None:
        res = await self._call(History(chat_jid=jid, n=min(n, 1000)))
        if res is None:
            return
        try:
            rows = parse_rows(MessageNew, res, "messages")
        except _BAD_REPLY:
            self.error("respuesta inválida del daemon")
            return
        self._last_day = None  # el historial abre con su fecha
        self.view = []
        for m in rows:
            self._remember(m)
            self._print_message(m)
        if not rows:
            self.note("sin mensajes todavía.")

    async def _cmd_search(self, query: str) -> None:
        res = await self._call(Search(q=query, limit=commands.TOP_SEARCH))
        if res is None:
            return
        try:
            hits = parse_rows(MessageNew, res, "messages")
        except _BAD_REPLY:
            self.error("respuesta inválida del daemon")
            return
        if not hits:
            self.note("sin resultados.")
            return
        self.listing = [m.chat_jid for m in hits]  # `/open <n>` salta al chat del resultado
        self._emit(*render.render_search_results(hits, self.name_of, self._pal, self._tz))

    async def _cmd_mute(self, cmd: commands.MuteChat) -> None:
        jid = await self._resolve(cmd.chat)
        if jid is None:
            return
        until = 0 if cmd.seconds == 0 else int(time.time()) + cmd.seconds
        if await self._call(Mute(chat_jid=jid, until=until)) is not None:
            self.chats[jid] = self.chats[jid].model_copy(update={"muted_until": until})
            verb = "silenciado" if until else "con sonido de nuevo"
            self.note(f"{render.truncate(self.name_of(jid), 40)}: {verb}.")


async def _pump(client: Client, repl: Repl) -> None:
    async for ev in client.events():
        try:
            repl.handle(ev)
        except Exception as e:  # un evento malo no debe dejar el REPL sordo
            repl.error(f"error interno al procesar un evento ({type(e).__name__})")


_PERSISTED: Final = (
    commands.ShowChats,
    commands.Open,
    commands.Back,
    commands.ShowHistory,
    commands.MuteChat,
    commands.Help,
    commands.Quit,
)


class _CommandHistory(FileHistory):
    """Persiste solo comandos de navegación: ni mensajes ni búsquedas (PII) van a disco."""

    def store_string(self, string: str) -> None:
        if isinstance(commands.parse(string), _PERSISTED):
            super().store_string(string)


def _history_file() -> FileHistory:
    """Historial de comandos en `~/.local/state/wsp/history` con permisos 600."""
    paths.ensure_dirs()
    path = paths.state_dir() / "history"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid():
            raise PermissionError("historial pertenece a otro usuario")
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return _CommandHistory(str(path))


async def run_loop(repl: Repl, session: PromptSession[str]) -> int:
    """Bucle de entrada. Ctrl+C limpia la línea; Ctrl+D (EOF) o /quit salen."""
    while True:
        try:
            line = await session.prompt_async(repl.prompt)
        except KeyboardInterrupt:
            continue
        except EOFError:
            return EXIT_OK
        try:
            if not await repl.handle_line(line):
                return EXIT_OK
        except Exception as e:  # sin traceback: el mensaje podría llevar contenido
            repl.error(f"error interno ({type(e).__name__})")


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
    notifications: bool = True,
    notifier: Notifier | None = None,
) -> int:
    """Abre el REPL. Códigos: 0 ok · 2 daemon sin respuesta · 3 cuenta sin vincular."""
    path = socket or paths.socket_path()
    async with Client(path, kind="cli") as client:
        try:
            await client.wait_connected(CONNECT_TIMEOUT)
        except ConnectionLost:
            offer_daemon_help(ask, out or sys.stdout)
            return EXIT_UNREACHABLE
        if notifier is None and notifications:
            notifier = notify.notify
        repl = Repl(client, out, pal, tz=tz, notifier=notifier if notifications else None)
        pump = asyncio.create_task(_pump(client, repl), name="wsp-pump")
        try:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(repl.account_seen.wait(), ACCOUNT_WAIT)
            if repl.account is not None and repl.account.state in _UNLINKED:
                (out or sys.stdout).write("wsp: la cuenta no está vinculada. Ejecuta `wsp pair`.\n")
                return EXIT_UNLINKED
            await repl.preload()
            if session is None:
                session = PromptSession(
                    history=_history_file(),
                    completer=WspCompleter(
                        lambda: [c.title for c in repl.chats.values() if c.title]
                    ),
                    complete_while_typing=False,
                )
            with patch_stdout(raw=True):
                repl.note("conectado. /help para ver comandos.")
                return await run_loop(repl, session)
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump
            await repl.aclose()
