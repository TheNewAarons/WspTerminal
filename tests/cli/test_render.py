"""Snapshots exactos de wsp.cli.render (funciones puras)."""

import zlib
from datetime import UTC, datetime

import pytest

from wsp.cli import render as r
from wsp.protocol.models import ChatUpdated, MessageNew, MessageStatus, Reaction

JUAN = "111@s.whatsapp.net"
ANA = "222@s.whatsapp.net"
GRP = "g1@g.us"
TS = int(datetime(2026, 10, 20, 14, 2, tzinfo=UTC).timestamp())

COLOR = r.Palette(True)
PLAIN = r.Palette(False)
NAMES = {JUAN: "Juan", ANA: "Ana", GRP: "Familia"}


def name_of(jid: str) -> str:
    return NAMES.get(jid, jid)


def msg(**kw: object) -> MessageNew:
    base: dict[str, object] = {"chat_jid": JUAN, "wa_id": "W1", "ts": TS, "body": "hola"}
    base.update(kw)
    return MessageNew(**base)  # type: ignore[arg-type]


def render(m: MessageNew, pal: r.Palette = PLAIN, **kw: object) -> list[str]:
    return r.render_message(m, name_of=name_of, pal=pal, tz=UTC, **kw)  # type: ignore[arg-type]


T = "\x1b[2m[14:02]\x1b[0m"


def test_direct() -> None:
    assert render(msg()) == ["[14:02] Juan › hola"]
    assert render(msg(), COLOR) == [f"{T} \x1b[33mJuan\x1b[0m › hola"]


def test_group() -> None:
    m = msg(chat_jid=GRP, sender_jid=ANA)
    assert render(m, group_title="Familia") == ["[14:02] Familia / Ana › hola"]
    assert render(m, COLOR, group_title="Familia") == [
        f"{T} \x1b[1mFamilia\x1b[0m / \x1b[92mAna\x1b[0m › hola"
    ]


@pytest.mark.parametrize(
    ("status", "error", "plain", "colored"),
    [
        ("pending", None, "…", "\x1b[2m…\x1b[0m"),
        ("sent", None, "✓", "\x1b[2m✓\x1b[0m"),
        ("delivered", None, "✓✓", "\x1b[2m✓✓\x1b[0m"),
        ("read", None, "✓✓", "\x1b[36m✓✓\x1b[0m"),
        ("failed", "sin red", "✗ sin red", "\x1b[31m✗ sin red\x1b[0m"),
        ("failed", None, "✗", "\x1b[31m✗\x1b[0m"),
    ],
)
def test_own_status(status: str, error: str | None, plain: str, colored: str) -> None:
    m = msg(from_me=True, status=status, error=error)
    assert render(m) == [f"[14:02] tú › hola {plain}"]
    assert render(m, COLOR) == [f"{T} \x1b[1mtú\x1b[0m › hola {colored}"]


def test_quote() -> None:
    q = msg(wa_id="Q", body="x" * 100)
    m = msg(from_me=True, status="sent", quoted_wa_id="Q")
    assert render(m, quoted=q) == ["  ↳ Juan: " + "x" * 59 + "…", "[14:02] tú › hola ✓"]
    assert render(m, COLOR, quoted=q)[0] == "\x1b[2m  ↳ Juan: " + "x" * 59 + "…\x1b[0m"
    assert render(msg(quoted_wa_id="Q")) == ["  ↳ (mensaje no disponible)", "[14:02] Juan › hola"]
    assert render(msg(quoted_wa_id="Q"), COLOR)[0] == "\x1b[2m  ↳ (mensaje no disponible)\x1b[0m"


def test_edited() -> None:
    m = msg(edited_at=TS + 5)
    assert render(m) == ["[14:02] Juan › hola (editado)"]
    assert render(m, COLOR) == [f"{T} \x1b[33mJuan\x1b[0m › hola \x1b[2m(editado)\x1b[0m"]


def test_deleted() -> None:
    m = msg(deleted_at=TS + 5, edited_at=TS + 1)
    assert render(m) == ["[14:02] Juan › (mensaje eliminado)"]
    assert render(m, COLOR) == [f"{T} \x1b[33mJuan\x1b[0m › \x1b[2m(mensaje eliminado)\x1b[0m"]


def test_media() -> None:
    assert render(msg(kind="image", body="caption")) == ["[14:02] Juan › [imagen] caption"]
    assert render(msg(kind="ptt", body=None)) == ["[14:02] Juan › [nota de voz]"]
    assert render(msg(kind="image", body="c"), COLOR) == [f"{T} \x1b[33mJuan\x1b[0m › [imagen] c"]


def test_multiline() -> None:
    m = msg(body="uno\ndos\r\ntres", from_me=True, status="sent")
    assert render(m) == ["[14:02] tú › uno", " " * 8 + "dos", " " * 8 + "tres ✓"]
    assert render(m, COLOR)[2] == " " * 8 + "tres \x1b[2m✓\x1b[0m"


def test_escape_injection() -> None:
    out = render(msg(body="\x1b[31mrojo\x1b[0m‮"))
    assert out == ["[14:02] Juan › [31mrojo[0m"]
    assert all("\x1b" not in line for line in out)


def test_date_separator() -> None:
    assert r.date_separator(TS, PLAIN, UTC) == "── martes 20 oct 2026 ──"
    assert r.date_separator(TS, COLOR, UTC) == "\x1b[2m── martes 20 oct 2026 ──\x1b[0m"


def test_reconnect_header() -> None:
    assert r.reconnect_header(0, PLAIN) == "── reconectado, sin mensajes nuevos ──"
    assert r.reconnect_header(1, PLAIN) == "── reconectado, 1 mensaje nuevo ──"
    assert r.reconnect_header(12, PLAIN) == "── reconectado, 12 mensajes nuevos ──"
    assert r.reconnect_header(12, COLOR) == "\x1b[2m── reconectado, 12 mensajes nuevos ──\x1b[0m"


def _st(status: str, error: str | None = None) -> MessageStatus:
    return MessageStatus(chat_jid=JUAN, wa_id="W1", status=status, error=error)  # type: ignore[arg-type]


def test_status_line() -> None:
    assert r.render_status_line(_st("delivered"), PLAIN) == "  ✓✓ entregado"
    assert r.render_status_line(_st("delivered"), COLOR) == "  \x1b[2m✓✓ entregado\x1b[0m"
    assert r.render_status_line(_st("failed", "sin red"), PLAIN) == "  ✗ fallido: sin red"
    assert (
        r.render_status_line(_st("failed", "sin red"), COLOR)
        == "  \x1b[31m✗ fallido: sin red\x1b[0m"
    )
    assert r.render_status_line(_st("read"), COLOR, "Juan") == "  \x1b[36m✓✓ leído (Juan)\x1b[0m"
    assert r.render_status_line(_st("sent"), PLAIN, "Juan") == "  ✓ enviado (Juan)"


def test_chat_list() -> None:
    chats = [
        ChatUpdated(jid=JUAN, kind="direct", title="Juan", unread_count=2),
        ChatUpdated(jid=GRP, kind="group", title="Familia", muted_until=5),
        ChatUpdated(jid=ANA, kind="direct"),
    ]
    assert r.render_chat_list(chats, PLAIN) == [
        "1  Juan  (2 sin leer)",
        "2  Familia  (silenciado, grupo)",
        "3  222",
    ]
    assert r.render_chat_list(chats, COLOR)[0] == (
        "\x1b[2m1\x1b[0m  \x1b[33mJuan\x1b[0m\x1b[2m  (2 sin leer)\x1b[0m"
    )


def test_reaction() -> None:
    rx = Reaction(chat_jid=JUAN, wa_id="W1", sender_jid=ANA, emoji="👍", ts=TS)
    assert r.render_reaction(rx, msg(), name_of, PLAIN) == '  Ana reaccionó 👍 a "hola"'
    assert r.render_reaction(rx, None, name_of, COLOR) == "\x1b[2m  Ana reaccionó 👍\x1b[0m"
    rm = rx.model_copy(update={"emoji": ""})
    assert r.render_reaction(rm, None, name_of, PLAIN) == "  Ana quitó su reacción"


def test_sender_color_stable() -> None:
    for jid, expected in [(JUAN, 33), (ANA, 92), (GRP, 33), ("x", 35)]:
        assert r.sender_color(jid) == expected
        assert r.sender_color(jid) == r.PALETTE[zlib.crc32(jid.encode()) % len(r.PALETTE)]
        assert expected in r.PALETTE


def test_color_enabled() -> None:
    assert r.color_enabled(False, {}, True)
    assert r.color_enabled(False, None, True)
    assert r.color_enabled(False, {"NO_COLOR": ""}, True)
    assert not r.color_enabled(False, {"NO_COLOR": "1"}, True)
    assert not r.color_enabled(True, {}, True)
    assert not r.color_enabled(False, {}, False)


def test_sanitize() -> None:
    assert r.sanitize("a\x1bb\x00c\x7fd\x85e‪f⁧g⁩h") == "abcdefgh"
    assert r.sanitize("a\tb") == "a    b"
    assert r.sanitize("a\r\nb\rc") == "a\nb\nc"
    assert r.sanitize("\x1b[31mx") == "[31mx"


def test_one_line_truncate() -> None:
    assert r.one_line("a\n b\t\x1bc ") == "a b c"
    assert r.truncate("abc", 5) == "abc"
    assert r.truncate("a" * 10, 5) == "aaaa…"
    assert len(r.truncate("a b " * 50)) <= r.QUOTE_MAX
