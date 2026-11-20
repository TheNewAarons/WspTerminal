"""Parser de comandos, duraciones y resolución de chats (funciones puras)."""

import pytest

from wsp.cli import commands as c
from wsp.protocol.models import ChatUpdated


def chat(jid: str, title: str | None = None, **kw: object) -> ChatUpdated:
    return ChatUpdated(jid=jid, kind="direct", title=title, **kw)  # type: ignore[arg-type]


# ---- parse -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("", c.Empty()),
        ("   ", c.Empty()),
        ("hola", c.Say("hola")),
        ("  hola  ", c.Say("hola")),
        ("//x", c.Say("/x")),
        ("//", c.Say("/")),
        ("/quit", c.Quit()),
        ("/exit", c.Quit()),
        ("/q", c.Quit()),
        ("/QUIT", c.Quit()),
        ("/help", c.Help()),
        ("/h", c.Help()),
        ("/?", c.Help()),
        ("/back", c.Back()),
        ("/chats", c.ShowChats(15)),
        ("/chats 5", c.ShowChats(5)),
        ("/chats 200", c.ShowChats(200)),
        ("/history", c.ShowHistory(20)),
        ("/history 50", c.ShowHistory(50)),
        ("/open Juan Pérez", c.Open("Juan Pérez")),
        ("/open 3", c.Open("3")),
        ("/search buenos días", c.SearchFor("buenos días")),
        ("/r 2 hola  mundo ", c.Reply(2, "hola  mundo")),
        ("/react 3 👍", c.ReactTo(3, "👍")),
        ("/mute Juan 30m", c.MuteChat("Juan", 1800)),
        ("/mute Familia Pérez 8h", c.MuteChat("Familia Pérez", 28800)),
        ("/mute Juan off", c.MuteChat("Juan", 0)),
    ],
)
def test_parse_ok(line: str, expected: c.Parsed) -> None:
    assert c.parse(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "/chats 0",
        "/chats 201",
        "/chats -1",
        "/chats x",
        "/chats ٣",  # dígito no ASCII
        "/history 0",
        "/history 201",
        "/open",
        "/search",
        "/r",
        "/r 1",
        "/r x hola",
        "/r 0 hola",
        "/r 1000000 hola",
        "/react",
        "/react 1",
        "/react x 👍",
        "/react 1 a b",
        "/react 1 " + "x" * 33,
        "/mute",
        "/mute Juan",
        "/mute Juan 5x",
        "/mute Juan 0m",
        "/mute 30m",
        "/mute Juan 9999w",
    ],
)
def test_parse_invalid_usage(line: str) -> None:
    res = c.parse(line)
    assert isinstance(res, c.Invalid)
    assert "uso:" in res.message


def test_parse_unknown_command_truncates_name() -> None:
    res = c.parse("/" + "z" * 100)
    assert isinstance(res, c.Invalid)
    assert res.message.startswith("comando desconocido: /" + "z" * 32)
    assert "z" * 33 not in res.message


def test_usage_matches_table() -> None:
    assert c.usage("chats") == "/chats [n]"
    with pytest.raises(StopIteration):
        c.usage("nope")


# ---- parse_duration --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "secs"),
    [("30m", 1800), ("8h", 28800), ("2d", 172800), ("1w", 604800), ("off", 0), (" OFF ", 0),
     ("1H", 3600), ("52w", 52 * 604800)],
)  # fmt: skip
def test_parse_duration_ok(text: str, secs: int) -> None:
    assert c.parse_duration(text) == secs


@pytest.mark.parametrize("text", ["", "30", "m", "1.5h", "-1h", "1y", "10000m", "1 h", "0h"])
def test_parse_duration_invalid(text: str) -> None:
    with pytest.raises(ValueError, match="duración"):
        c.parse_duration(text)


def test_parse_duration_excess() -> None:
    with pytest.raises(ValueError, match="fuera de rango"):
        c.parse_duration("53w")


# ---- resolve_chat ----------------------------------------------------------------------------

J, F, A = "111@s.whatsapp.net", "999@g.us", "222@s.whatsapp.net"


def mapping(*chats: ChatUpdated) -> dict[str, ChatUpdated]:
    return {x.jid: x for x in chats}


def test_number_in_range() -> None:
    assert c.resolve_chat("2", {}, [J, F]) == c.Found(F)


def test_number_out_of_range() -> None:
    assert c.resolve_chat("3", {}, [J, F]) == c.OutOfRange(3, 2)
    assert c.resolve_chat("1", {}, []) == c.OutOfRange(1, 0)


def test_empty_query_not_found() -> None:
    assert c.resolve_chat("  ", mapping(chat(J, "Juan")), []) == c.NotFound()


def test_literal_jid_case_insensitive() -> None:
    chats = mapping(chat(J, "Otro"))
    assert c.resolve_chat(J.upper(), chats, []) == c.Found(J)


@pytest.mark.parametrize("q", ["juan", "JUAN", "juam"])
def test_unique_fuzzy_by_title(q: str) -> None:
    chats = mapping(chat(J, "Juan"), chat(F, "Familia"))
    assert c.resolve_chat(q, chats, []) == c.Found(J)


def test_match_by_alias_and_push_name() -> None:
    chats = mapping(chat(J, None, alias="Mamá"), chat(A, None, push_name="Roberto"), chat(F, "Zzz"))
    assert c.resolve_chat("mamá", chats, []) == c.Found(J)
    assert c.resolve_chat("roberto", chats, []) == c.Found(A)


def test_not_found() -> None:
    chats = mapping(chat(J, "Juan"))
    assert c.resolve_chat("qqqqqq", chats, []) == c.NotFound()


def test_exact_wins_over_similar() -> None:
    chats = mapping(chat(J, "Ana"), chat(A, "Ana María"), chat(F, "Anabel"))
    assert c.resolve_chat("ana", chats, []) == c.Found(J)


def test_clear_advantage_opens() -> None:
    chats = mapping(chat(J, "Juan Carlos"), chat(A, "Juanita Lopez Ruiz"))
    res = c.resolve_chat("juan carlos", chats, [])
    assert res == c.Found(J)


def test_ambiguous_returns_top5_best_first() -> None:
    chats = mapping(*(chat(f"{i}@s.whatsapp.net", f"Carlos {n}") for i, n in enumerate("abcdefg")))
    res = c.resolve_chat("carlos", chats, [])
    assert isinstance(res, c.Ambiguous)
    assert len(res.jids) == c.TOP_AMBIGUOUS
    assert len(set(res.jids)) == c.TOP_AMBIGUOUS


def test_ambiguous_tiebreak_pinned_then_recent() -> None:
    a = chat("1@s.whatsapp.net", "Pedro Uno", last_message_at=100)
    b = chat("2@s.whatsapp.net", "Pedro Dos", last_message_at=200)
    p = chat("3@s.whatsapp.net", "Pedro Tres", last_message_at=50, pinned=True)
    res = c.resolve_chat("pedro", mapping(a, b, p), [])
    assert isinstance(res, c.Ambiguous)
    assert res.jids[0] == p.jid
    assert res.jids[1:] == (b.jid, a.jid)
