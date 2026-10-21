"""Autocompletado del REPL: comandos, chats y menciones `@`."""

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from wsp.cli import mentions
from wsp.cli.completer import WspCompleter

ANA = "5215511110001@s.whatsapp.net"
ANA2 = "5215511110002@s.whatsapp.net"
LUIS = "5215522220002@s.whatsapp.net"
LID = "258677357432950@lid"
MEMBERS: list[mentions.Member] = [
    (ANA, "Ana Pérez"),
    (ANA2, "Ana Ruiz"),
    (LUIS, "Luis Gómez"),
    (LID, None),
]


def _complete(text: str, members: list[mentions.Member] | None = None) -> list[tuple[str, int]]:
    c = WspCompleter(lambda: ["Familia"], (lambda: members) if members is not None else None)
    return [
        (x.text, x.start_position)
        for x in c.get_completions(Document(text), CompleteEvent(text_inserted=True))
    ]


def test_at_lists_all_members() -> None:
    got = _complete("hola @", MEMBERS)
    assert len(got) == 4
    assert all(start == -1 for _t, start in got)


def test_at_filters_by_name_ignoring_case_and_accents() -> None:
    assert [t for t, _ in _complete("@gom", MEMBERS)] == ["@Luis "]
    assert [t for t, _ in _complete("@PEREZ", MEMBERS)] == ["@Pérez "]


def test_ambiguous_first_name_uses_unique_word() -> None:
    assert [t for t, _ in _complete("@ana", MEMBERS)] == ["@Pérez ", "@Ruiz "]


def test_nameless_member_uses_number() -> None:
    assert [t for t, _ in _complete("@2586", MEMBERS)] == [f"@{LID.split('@')[0]} "]


def test_replaces_only_the_query() -> None:
    assert _complete("hola @lu", MEMBERS) == [("@Luis ", -3)]


def test_no_completion_for_email_or_plain_text() -> None:
    assert _complete("mail a@lu", MEMBERS) == []
    assert _complete("hola lu", MEMBERS) == []


def test_no_members_provider_or_direct_chat() -> None:
    assert _complete("@a") == []
    assert _complete("@a", []) == []


def test_chat_titles_and_commands_still_work() -> None:
    assert [t for t, _ in _complete("/op")] == ["open"]
    assert [t for t, _ in _complete("/open fam")] == ["Familia"]
