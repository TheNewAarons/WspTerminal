"""Menciones `@nombre` en grupos: resuelve contra los miembros y arma texto + lista de JIDs."""

import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from wsp.cli import render

_TOKEN: Final = re.compile(r"(?<![\w@])@(\w[\w.-]{0,63})")
GROUP_SUFFIX: Final = "@g.us"
TOP_CANDIDATES: Final = 5

Member = tuple[str, str | None]  # (jid, nombre visible)
FetchMembers = Callable[[str], Awaitable[list[Member] | None]]


class MentionError(Exception):
    """Mención que no se pudo resolver; el mensaje se muestra tal cual al usuario."""


@dataclass(frozen=True, slots=True)
class Prepared:
    text: str
    mentions: list[str]


def _fold(s: str) -> str:
    """Minúsculas y sin acentos, para comparar nombres."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(c)
    )


def _user(jid: str) -> str:
    return jid.split("@", 1)[0]


def _candidates(token: str, members: list[Member]) -> list[Member]:
    t = _fold(token)
    if t.isdigit():
        return [m for m in members if _user(m[0]).endswith(t)]
    words = [(m, _fold(m[1]).split()) for m in members if m[1]]
    exact = [m for m, ws in words if t in ws]
    return exact or [m for m, ws in words if any(w.startswith(t) for w in ws)]


def _handle(jid: str, name: str | None, members: list[Member]) -> str:
    """Texto que va tras `@` y resuelve sin ambigüedad a este miembro."""
    for word in (name or "").split():
        w = word.strip(".,;:!?()[]{}\"'")
        if _TOKEN.fullmatch(f"@{w}") and [m[0] for m in _candidates(w, members)] == [jid]:
            return w
    return _user(jid)


def suggestions(query: str, members: list[Member]) -> list[tuple[str, str]]:
    """(handle, nombre visible) de los miembros que coinciden con lo escrito tras `@`."""
    q = _fold(query)
    out: list[tuple[str, str]] = []
    for jid, name in members:
        label = name or _user(jid)
        if q in _fold(label) or (q.isdigit() and q in _user(jid)):
            out.append((_handle(jid, name, members), label))
    return sorted(out, key=lambda s: _fold(s[1]))


def has_mention_token(text: str) -> bool:
    return _TOKEN.search(text) is not None


def resolve_mentions(text: str, members: list[Member]) -> Prepared:
    """Reemplaza `@nombre` por `@<user>`; sin coincidencia se deja literal; ambiguo ⇒ error."""
    mentions: list[str] = []

    def repl(m: re.Match[str]) -> str:
        token = m.group(1)
        found = _candidates(token, members)
        if not found:
            return m.group(0)
        if len({j for j, _ in found}) > 1:
            names = ", ".join(render.one_line(n or _user(j)) for j, n in found[:TOP_CANDIDATES])
            raise MentionError(f"@{token} es ambiguo ({names}); escribe más letras o el número")
        jid = found[0][0]
        if jid not in mentions:
            mentions.append(jid)
        return f"@{_user(jid)}"

    return Prepared(_TOKEN.sub(repl, text), mentions)


async def prepare(text: str, chat_jid: str, fetch_members: FetchMembers) -> Prepared:
    """Solo en grupos y solo si el texto trae `@algo`; si no, el texto pasa intacto."""
    if not chat_jid.endswith(GROUP_SUFFIX) or not has_mention_token(text):
        return Prepared(text, [])
    members = await fetch_members(chat_jid)
    if members is None:
        raise MentionError("no se pudo leer los miembros del grupo para resolver las menciones")
    return resolve_mentions(text, members)
