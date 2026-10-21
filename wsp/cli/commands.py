"""Parser de comandos del REPL y resolución de chats: funciones puras (sin I/O)."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from rapidfuzz import fuzz

from wsp.protocol.models import ChatUpdated

DEFAULT_CHATS: Final = 15
DEFAULT_HISTORY: Final = 20
MAX_COUNT: Final = 200
TOP_AMBIGUOUS: Final = 5
TOP_SEARCH: Final = 20
FUZZY_CUTOFF: Final = 60
_CLEAR_WIN: Final = 10  # ventaja mínima del mejor candidato para abrir sin preguntar
_EMOJI_MAX: Final = 32
_DURATION_RE: Final = re.compile(r"^(\d{1,4})([mhdw])$")
_UNIT: Final = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
_MAX_MUTE: Final = 52 * 604800

# (comando, uso, descripción): fuente única para /help y el autocompletado.
COMMANDS: Final[tuple[tuple[str, str, str], ...]] = (
    ("chats", "/chats [n]", "lista los n chats más recientes (15 por defecto)"),
    ("open", "/open <n|nombre>", "abre un chat: número de la última lista o nombre aproximado"),
    ("back", "/back", "cierra el chat abierto"),
    ("r", "/r <n> <texto>", "responde al mensaje n de la vista actual"),
    ("react", "/react <n> <emoji>", "reacciona al mensaje n de la vista actual"),
    ("search", "/search <texto>", "busca en todos los chats (luego /open <n> salta al chat)"),
    ("mute", "/mute <chat> <30m|8h|1w|off>", "silencia un chat o quita el silencio"),
    ("history", "/history [n]", "muestra los últimos n mensajes del chat abierto (20)"),
    ("help", "/help", "esta ayuda"),
    ("quit", "/quit", "salir (también Ctrl+D)"),
)
_ALIASES: Final = {"exit": "quit", "q": "quit", "h": "help", "?": "help"}


@dataclass(frozen=True)
class Say:
    text: str


@dataclass(frozen=True)
class ShowChats:
    n: int


@dataclass(frozen=True)
class Open:
    query: str


@dataclass(frozen=True)
class Back:
    pass


@dataclass(frozen=True)
class Reply:
    n: int
    text: str


@dataclass(frozen=True)
class ReactTo:
    n: int
    emoji: str


@dataclass(frozen=True)
class SearchFor:
    query: str


@dataclass(frozen=True)
class MuteChat:
    chat: str
    seconds: int  # 0 = quitar silencio


@dataclass(frozen=True)
class ShowHistory:
    n: int


@dataclass(frozen=True)
class Help:
    pass


@dataclass(frozen=True)
class Quit:
    pass


@dataclass(frozen=True)
class Invalid:
    """Línea inválida; `message` es lo que se muestra al usuario."""

    message: str


@dataclass(frozen=True)
class Empty:
    pass


Parsed = (
    Say
    | ShowChats
    | Open
    | Back
    | Reply
    | ReactTo
    | SearchFor
    | MuteChat
    | ShowHistory
    | Help
    | Quit
    | Invalid
    | Empty
)


def usage(name: str) -> str:
    return next(u for n, u, _ in COMMANDS if n == name)


def parse_duration(text: str) -> int:
    """`30m|8h|2d|1w` → segundos; `off` → 0. ValueError si es inválida o excesiva."""
    t = text.strip().lower()
    if t == "off":
        return 0
    m = _DURATION_RE.match(t)
    if m is None:
        raise ValueError("duración inválida")
    secs = int(m.group(1)) * _UNIT[m.group(2)]
    if not 0 < secs <= _MAX_MUTE:
        raise ValueError("duración fuera de rango")
    return secs


def _count(arg: str, default: int) -> int | None:
    if not arg:
        return default
    return int(arg) if arg.isascii() and arg.isdigit() and 0 < int(arg) <= MAX_COUNT else None


def _number(token: str) -> int | None:
    return int(token) if token.isascii() and token.isdigit() and 0 < int(token) < 10**6 else None


def parse(line: str) -> Parsed:
    """Línea del usuario → comando tipado. Texto sin `/` es `Say`."""
    text = line.strip()
    if not text:
        return Empty()
    if not text.startswith("/") or text.startswith("//"):
        return Say(text.removeprefix("/") if text.startswith("//") else text)
    name, _, arg = text[1:].partition(" ")
    name = _ALIASES.get(name.lower(), name.lower())
    arg = arg.strip()
    match name:
        case "quit":
            return Quit()
        case "help":
            return Help()
        case "back":
            return Back()
        case "chats":
            n = _count(arg, DEFAULT_CHATS)
            return Invalid(f"uso: {usage('chats')}") if n is None else ShowChats(n)
        case "history":
            n = _count(arg, DEFAULT_HISTORY)
            return Invalid(f"uso: {usage('history')}") if n is None else ShowHistory(n)
        case "open":
            return Open(arg) if arg else Invalid(f"uso: {usage('open')}")
        case "search":
            return SearchFor(arg) if arg else Invalid(f"uso: {usage('search')}")
        case "r":
            head, _, body = arg.partition(" ")
            num = _number(head)
            if num is None or not body.strip():
                return Invalid(f"uso: {usage('r')}")
            return Reply(num, body.strip())
        case "react":
            head, _, emoji = arg.partition(" ")
            num, emoji = _number(head), emoji.strip()
            if num is None or not emoji or len(emoji) > _EMOJI_MAX or " " in emoji:
                return Invalid(f"uso: {usage('react')}")
            return ReactTo(num, emoji)
        case "mute":
            chat, _, dur = arg.rpartition(" ")
            if not chat.strip():
                return Invalid(f"uso: {usage('mute')}")
            try:
                return MuteChat(chat.strip(), parse_duration(dur))
            except ValueError:
                return Invalid(f"duración inválida (30m, 8h, 1w, off). uso: {usage('mute')}")
        case _:
            return Invalid(f"comando desconocido: /{name[:32]} (/help)")


# ---- resolución de chats ----------------------------------------------------------------------


@dataclass(frozen=True)
class Found:
    jid: str


@dataclass(frozen=True)
class Ambiguous:
    jids: tuple[str, ...]  # top 5, mejor primero


@dataclass(frozen=True)
class NotFound:
    pass


@dataclass(frozen=True)
class OutOfRange:
    n: int
    size: int  # 0 = no hay lista mostrada todavía


Resolution = Found | Ambiguous | NotFound | OutOfRange


def _names(c: ChatUpdated) -> list[str]:
    return [s.lower() for s in (c.title, c.alias, c.push_name) if s]


def _score(query: str, c: ChatUpdated) -> float:
    return max((fuzz.WRatio(query, n) for n in _names(c)), default=0.0)


def resolve_chat(
    query: str, chats: Mapping[str, ChatUpdated], listing: Sequence[str]
) -> Resolution:
    """Número ⇒ posición en `listing`; si no, búsqueda difusa (título, alias, push_name).

    Abre directo si hay un candidato claro (único, coincidencia exacta o ventaja ≥ 10 puntos);
    si no, `Ambiguous` con el top 5.
    """
    q = query.strip().lower()
    if not q:
        return NotFound()
    n = _number(q)
    if n is not None:
        return Found(listing[n - 1]) if n <= len(listing) else OutOfRange(n, len(listing))
    for c in chats.values():  # jid literal
        if c.jid.lower() == q:
            return Found(c.jid)
    scored = sorted(
        ((s, c) for c in chats.values() if (s := _score(q, c)) >= FUZZY_CUTOFF),
        key=lambda t: (-t[0], not t[1].pinned, -(t[1].last_message_at or 0)),
    )
    if not scored:
        return NotFound()
    exact = [c for _, c in scored if q in _names(c)]
    if len(exact) == 1:
        return Found(exact[0].jid)
    if len(scored) == 1 or scored[0][0] - scored[1][0] >= _CLEAR_WIN:
        return Found(scored[0][1].jid)
    return Ambiguous(tuple(c.jid for _, c in scored[:TOP_AMBIGUOUS]))
