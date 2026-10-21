"""Autocompletado del REPL: comandos y nombres de chat."""

import re
from collections.abc import Callable, Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from wsp.cli import mentions, render
from wsp.cli.commands import COMMANDS

_CHAT_ARG = frozenset({"open", "mute"})
_AT_QUERY = re.compile(r"(?<![\w@])@([\w.-]*)$")
MAX_SUGGESTIONS = 30


class WspCompleter(Completer):
    """`/` + prefijo ⇒ comandos; tras `/open` o `/mute` ⇒ títulos de chat (sin distinguir
    mayúsculas, coincidencia por prefijo o por subcadena)."""

    def __init__(
        self,
        titles: Callable[[], Iterable[str]],
        members: Callable[[], list[mentions.Member]] | None = None,
    ) -> None:
        self._titles = titles
        self._members = members

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
            yield from self._mentions(text)
            return
        head, sep, arg = text[1:].partition(" ")
        if not sep:
            for name, _usage, desc in COMMANDS:
                if name.startswith(head.lower()):
                    yield Completion(name, -len(head), display_meta=desc)
            return
        if head.lower() not in _CHAT_ARG:
            return
        q = arg.lower()
        seen: set[str] = set()
        for title in sorted(self._titles(), key=str.lower):
            t = title.strip()
            if t and t.lower() not in seen and q in t.lower():
                seen.add(t.lower())
                yield Completion(t, -len(arg))

    def _mentions(self, text: str) -> Iterable[Completion]:
        """`@` + letras en un grupo abierto ⇒ miembros (inserta un `@handle` sin ambigüedad)."""
        m = _AT_QUERY.search(text)
        if m is None or self._members is None:
            return
        query = m.group(1)
        for handle, label in mentions.suggestions(query, self._members())[:MAX_SUGGESTIONS]:
            yield Completion(f"@{handle} ", -len(query) - 1, display=render.one_line(label))
