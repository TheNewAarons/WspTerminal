"""Autocompletado del REPL: comandos y nombres de chat."""

from collections.abc import Callable, Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from wsp.cli.commands import COMMANDS

_CHAT_ARG = frozenset({"open", "mute"})


class WspCompleter(Completer):
    """`/` + prefijo ⇒ comandos; tras `/open` o `/mute` ⇒ títulos de chat (sin distinguir
    mayúsculas, coincidencia por prefijo o por subcadena)."""

    def __init__(self, titles: Callable[[], Iterable[str]]) -> None:
        self._titles = titles

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
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
