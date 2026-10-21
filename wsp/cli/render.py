"""Formato de salida del REPL: funciones puras (sin I/O) y testeables.

Todo texto que viene de la red (cuerpos, títulos, nombres) pasa por `sanitize` antes de
imprimirse, para que un mensaje no pueda inyectar secuencias de escape en la terminal.
"""

import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Final

from wsp.protocol.models import ChatUpdated, MessageNew, MessageStatus, Reaction

QUOTE_MAX: Final = 60
INDENT: Final = " " * 8
BULLET: Final = "›"

# Paleta sobria de 8 colores ANSI (sin rojo: se reserva para errores).
PALETTE: Final[tuple[int, ...]] = (32, 33, 34, 35, 36, 92, 93, 94)
_DIM: Final = 2
_BOLD: Final = 1
_RED: Final = 31
_CYAN: Final = 36

_DAYS: Final = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MONTHS: Final = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)
_MEDIA: Final = {
    "image": "imagen",
    "video": "video",
    "audio": "audio",
    "ptt": "nota de voz",
    "sticker": "sticker",
    "document": "documento",
    "location": "ubicación",
    "contact": "contacto",
}
_STATUS_TEXT: Final = {
    "pending": "… pendiente",
    "sent": "✓ enviado",
    "delivered": "✓✓ entregado",
    "read": "✓✓ leído",
}

NameOf = Callable[[str], str]


@dataclass(frozen=True)
class Palette:
    """Pinta con ANSI solo si `enabled`; con `enabled=False` devuelve el texto intacto."""

    enabled: bool = False

    def paint(self, text: str, *codes: int) -> str:
        if not self.enabled or not codes or not text:
            return text
        return f"\x1b[{';'.join(str(c) for c in codes)}m{text}\x1b[0m"

    def dim(self, text: str) -> str:
        return self.paint(text, _DIM)


def color_enabled(no_color_flag: bool, environ: dict[str, str] | None, is_tty: bool) -> bool:
    """Color solo en terminal real, sin `--no-color` y con `NO_COLOR` vacío o ausente."""
    env = environ or {}
    return is_tty and not no_color_flag and not env.get("NO_COLOR")


def sanitize(text: str) -> str:
    """Quita controles C0/C1 (salvo `\\n`), ESC, DEL y controles bidi; el tab pasa a 4 espacios."""
    out: list[str] = []
    for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
        o = ord(ch)
        if ch == "\n":
            out.append(ch)
        elif ch == "\t":
            out.append("    ")
        elif o < 0x20 or 0x7F <= o <= 0x9F or 0x202A <= o <= 0x202E or 0x2066 <= o <= 0x2069:
            continue
        else:
            out.append(ch)
    return "".join(out)


def one_line(text: str) -> str:
    """Texto sanitizado en una sola línea."""
    return " ".join(sanitize(text).split())


def truncate(text: str, limit: int = QUOTE_MAX) -> str:
    """Una línea de como mucho `limit` caracteres; recorta con `…`."""
    flat = one_line(text)
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def short_jid(jid: str) -> str:
    """Parte local del jid (lo que va antes de `@`), ya sanitizada."""
    return one_line(jid.split("@", 1)[0]) or "?"


def sender_color(jid: str) -> int:
    """Color ANSI estable por jid (crc32, no `hash()`, que varía entre procesos)."""
    return PALETTE[zlib.crc32(jid.encode()) % len(PALETTE)]


def _local(ts: int, tz: tzinfo | None) -> datetime:
    return datetime.fromtimestamp(ts, UTC).astimezone(tz)


def format_time(ts: int, tz: tzinfo | None = None) -> str:
    return _local(ts, tz).strftime("%H:%M")


def day_key(ts: int, tz: tzinfo | None = None) -> tuple[int, int, int]:
    d = _local(ts, tz)
    return d.year, d.month, d.day


def date_separator(ts: int, pal: Palette, tz: tzinfo | None = None) -> str:
    d = _local(ts, tz)
    label = f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]} {d.year}"
    return pal.dim(f"── {label} ──")


def separator(text: str, pal: Palette) -> str:
    return pal.dim(f"── {text} ──")


def reconnect_header(count: int, pal: Palette) -> str:
    if count == 0:
        return separator("reconectado, sin mensajes nuevos", pal)
    noun = "mensaje nuevo" if count == 1 else "mensajes nuevos"
    return separator(f"reconectado, {count} {noun}", pal)


def status_suffix(status: str, error: str | None, pal: Palette) -> str:
    """Marca de estado de un mensaje propio: ✓ · ✓✓ · ✗ motivo."""
    match status:
        case "failed":
            return pal.paint(f"✗ {one_line(error)}" if error else "✗", _RED)
        case "read":
            return pal.paint("✓✓", _CYAN)
        case "delivered":
            return pal.dim("✓✓")
        case "sent":
            return pal.dim("✓")
        case _:
            return pal.dim("…")


def render_status_line(update: MessageStatus, pal: Palette, chat_title: str | None = None) -> str:
    """Línea compacta de actualización de estado: `  ✓✓ entregado`."""
    if update.status == "failed":
        reason = f": {one_line(update.error)}" if update.error else ""
        text = f"✗ fallido{reason}"
        color: tuple[int, ...] = (_RED,)
    else:
        text = _STATUS_TEXT[update.status]
        color = (_CYAN,) if update.status == "read" else (_DIM,)
    where = f" ({truncate(chat_title, 30)})" if chat_title else ""
    return "  " + pal.paint(text + where, *color)


def _label(name: str, jid: str, pal: Palette, *, mine: bool) -> str:
    if mine:
        return pal.paint(name, _BOLD)
    return pal.paint(name, sender_color(jid))


def _media_prefix(kind: str) -> str:
    if kind == "text":
        return ""
    return f"[{_MEDIA.get(kind, one_line(kind))}]"


def _body(m: MessageNew) -> str:
    if m.deleted_at is not None:
        return "(mensaje eliminado)"
    text = sanitize(m.body or "").strip("\n")
    prefix = _media_prefix(m.kind)
    return f"{prefix} {text}".strip() if prefix else text


def author_name(m: MessageNew, name_of: NameOf) -> str:
    """Nombre visible del autor: `tú` si es propio, si no el sender (o el chat si es directo)."""
    return "tú" if m.from_me else name_of(m.sender_jid or m.chat_jid)


def render_quote(quoted: MessageNew | None, name_of: NameOf, pal: Palette) -> str:
    """`  ↳ Juan: texto citado…` (truncado a 60 caracteres)."""
    if quoted is None:
        return pal.dim("  ↳ (mensaje no disponible)")
    body = _body(quoted) or "…"
    return pal.dim(f"  ↳ {author_name(quoted, name_of)}: {truncate(body)}")


def render_message(
    m: MessageNew,
    *,
    name_of: NameOf,
    pal: Palette,
    group_title: str | None = None,
    quoted: MessageNew | None = None,
    tz: tzinfo | None = None,
) -> list[str]:
    """Líneas de un mensaje. `group_title` no nulo ⇒ `[hh:mm] Grupo / Autor › texto`.

    Multilínea: las líneas siguientes llevan sangría fija. Si `quoted_wa_id` está presente se
    antepone la línea de cita (`quoted=None` ⇒ mensaje citado no disponible).
    """
    who = author_name(m, name_of)
    who_jid = m.chat_jid if m.sender_jid is None else m.sender_jid
    label = _label(who, who_jid, pal, mine=m.from_me)
    if group_title is not None:
        label = f"{pal.paint(truncate(group_title, 40), _BOLD)} / {label}"
    head = f"{pal.dim(f'[{format_time(m.ts, tz)}]')} {label} {BULLET} "

    lines = _body(m).split("\n")
    tail: list[str] = []
    if m.deleted_at is None and m.edited_at is not None:
        tail.append(pal.dim("(editado)"))
    if m.from_me:
        tail.append(status_suffix(m.status, m.error, pal))
    if tail:
        lines[-1] = " ".join(p for p in (lines[-1], *tail) if p)
    if m.deleted_at is not None:
        lines[0] = pal.dim(lines[0])
    out = [head + lines[0], *(INDENT + line for line in lines[1:])]
    if m.quoted_wa_id is not None:
        out.insert(0, render_quote(quoted, name_of, pal))
    return out


def render_reaction(r: Reaction, target: MessageNew | None, name_of: NameOf, pal: Palette) -> str:
    who = name_of(r.sender_jid)
    what = f' a "{truncate(_body(target), 40)}"' if target is not None else ""
    text = f"{who} reaccionó {one_line(r.emoji)}{what}" if r.emoji else f"{who} quitó su reacción"
    return pal.dim(f"  {text}")


def render_chat_list(chats: Sequence[ChatUpdated], pal: Palette) -> list[str]:
    """Lista numerada (desde 1) para `/chats`; marca no leídos y silenciados."""
    width = len(str(len(chats)))
    out: list[str] = []
    for i, c in enumerate(chats, 1):
        title = truncate(c.title, 40) if c.title else short_jid(c.jid)
        extra: list[str] = []
        if c.unread_count:
            extra.append(f"{c.unread_count} sin leer")
        if c.muted_until:
            extra.append("silenciado")
        if c.kind == "group":
            extra.append("grupo")
        note = pal.dim(f"  ({', '.join(extra)})") if extra else ""
        out.append(f"{pal.dim(str(i).rjust(width))}  {pal.paint(title, sender_color(c.jid))}{note}")
    return out


HELP_NO_CHAT: Final = (
    "Sin chat abierto. Escribe /chats para ver tus chats y /open <n|nombre> para abrir uno.",
    "Texto sin `/` se envía al chat abierto. /help lista todos los comandos.",
)

HELP_TEXT: Final = (
    "/chats [n]           lista los n chats más recientes (30 por defecto)",
    "/open <n|nombre>     abre un chat (número de /chats o nombre aproximado)",
    "/back                cierra el chat abierto",
    "/history [n]         vuelve a mostrar los últimos n mensajes del chat abierto",
    "/help                esta ayuda",
    "/quit                salir (también Ctrl+D)",
    "(texto sin /)        se envía al chat abierto",
)
