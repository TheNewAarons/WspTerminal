import json
from typing import Any

import pytest

from wsp.protocol.models import (
    COMMAND_CLASSES,
    EVENT_CLASSES,
    MAX_LINE,
    AccountState,
    BackfillEnd,
    BackfillStart,
    ChatUpdated,
    Command,
    Envelope,
    ErrorEvent,
    Event,
    Hello,
    History,
    ListChats,
    MarkRead,
    MessageDeleted,
    MessageEdited,
    MessageNew,
    MessageStatus,
    Mute,
    Ping,
    Presence,
    ProtocolError,
    React,
    Reaction,
    Search,
    SendReply,
    SendText,
    decode_line,
    encode,
    parse_command,
    parse_event,
    to_envelope,
)

JID = "5215500000000@s.whatsapp.net"
SECRET = "SECRETO-PII-123"  # noqa: S105

EVENTS: list[Event] = [
    MessageNew(chat_jid=JID, wa_id="1", ts=10, body="hola ñ 😀", from_me=True),
    MessageStatus(chat_jid=JID, wa_id="1", status="failed", error="x"),
    MessageEdited(chat_jid=JID, wa_id="1", body="b", edited_at=11),
    MessageDeleted(chat_jid=JID, wa_id="1", deleted_at=12),
    Reaction(chat_jid=JID, wa_id="1", sender_jid=JID, emoji="👍", ts=13),
    ChatUpdated(jid=JID, kind="direct", title="Juan", unread_count=2),
    Presence(jid=JID, state="composing"),
    AccountState(state="connected"),
    BackfillStart(since=0),
    BackfillEnd(count=3, truncated=True),
    ErrorEvent(code="c", message="m"),
]
COMMANDS: list[Command] = [
    Hello(client="cli"),
    Hello(since=5, client="tail"),
    Ping(),
    SendText(chat_jid=JID, text="hola"),
    SendReply(chat_jid=JID, quoted_wa_id="9", text="re"),
    React(chat_jid=JID, wa_id="1", emoji="👍"),
    MarkRead(chat_jid=JID),
    ListChats(limit=5),
    History(chat_jid=JID, n=3),
    Search(q="algo", limit=10),
    Mute(chat_jid=JID, until=0),
]


def _code(line: bytes) -> str:
    with pytest.raises(ProtocolError) as ei:
        decode_line(line)
    return ei.value.code


# ---- decode_line ---------------------------------------------------------------------------


def test_line_too_big() -> None:
    assert _code(b"x" * (MAX_LINE + 1) + b"\n") == "line_too_big"


def test_line_at_limit_is_not_too_big() -> None:
    assert _code(b"x" * MAX_LINE + b"\n") == "bad_json"


def test_bad_encoding() -> None:
    assert _code(b'{"v":1,"type":"\xff\xfe"}\n') == "bad_encoding"


@pytest.mark.parametrize(
    "line",
    [b"{no json\n", b"\n", b'{"v":NaN}\n', b'{"v":1,"type":"x","data":{"a":Infinity}}\n'],
)
def test_bad_json(line: bytes) -> None:
    assert _code(line) == "bad_json"


@pytest.mark.parametrize(
    "line",
    [
        b"[1,2]\n",
        b'"texto"\n',
        b"42\n",
        b'{"v":1}\n',  # falta type
        b'{"v":1,"type":"x","extra":1}\n',
        b'{"v":1,"type":5}\n',
        b'{"v":1,"type":"x","data":[1]}\n',
        b'{"v":1,"type":"x","request_id":"con espacio"}\n',
        b'{"v":1,"type":"x","request_id":7}\n',
    ],
)
def test_bad_envelope(line: bytes) -> None:
    assert _code(line) == "bad_envelope"


@pytest.mark.parametrize(
    "line", [b'{"v":2,"type":"x"}\n', b'{"v":"1","type":"x"}\n', b'{"type":"x"}\n']
)
def test_unsupported_version(line: bytes) -> None:
    assert _code(line) == "unsupported_version"


def test_decode_accepts_without_trailing_newline() -> None:
    env = decode_line(b'{"v":1,"type":"ping","request_id":"r1"}')
    assert (env.type, env.request_id, env.data) == ("ping", "r1", {})


# ---- parse_command / parse_event ----------------------------------------------------------


def _cmd(type_: str, rid: str | None, data: dict[str, Any]) -> Envelope:
    return Envelope(type=type_, request_id=rid, data=data)


def test_unknown_command_and_event() -> None:
    with pytest.raises(ProtocolError) as ei:
        parse_command(_cmd("nope", "r1", {}))
    assert ei.value.code == "unknown_type"
    with pytest.raises(ProtocolError) as ei2:
        parse_event(_cmd("nope", None, {}))
    assert ei2.value.code == "unknown_type"


def test_missing_request_id() -> None:
    with pytest.raises(ProtocolError) as ei:
        parse_command(_cmd("ping", None, {}))
    assert ei.value.code == "missing_request_id"


@pytest.mark.parametrize(
    ("type_", "data"),
    [
        ("list_chats", {"limit": 0}),
        ("list_chats", {"limit": 1001}),
        ("list_chats", {"limit": "5"}),
        ("list_chats", {}),
        ("history", {"chat_jid": JID, "n": 0}),
        ("send_text", {"chat_jid": JID, "text": ""}),
        ("send_text", {"chat_jid": JID, "text": "x" * 65_537}),
        ("send_text", {"chat_jid": JID, "text": "hola", "extra": 1}),
        ("ping", {"extra": 1}),
        ("hello", {"client": "otro"}),
        ("mute", {"chat_jid": JID, "until": -1}),
    ],
)
def test_bad_payload(type_: str, data: dict[str, Any]) -> None:
    with pytest.raises(ProtocolError) as ei:
        parse_command(_cmd(type_, "r1", data))
    assert ei.value.code == "bad_payload"


def test_bad_event_payload() -> None:
    with pytest.raises(ProtocolError) as ei:
        parse_event(_cmd("message.new", None, {"chat_jid": JID}))
    assert ei.value.code == "bad_payload"


# ---- roundtrip -----------------------------------------------------------------------------


def test_samples_cover_every_class() -> None:
    assert {type(e) for e in EVENTS} == set(EVENT_CLASSES)
    assert {type(c) for c in COMMANDS} == set(COMMAND_CLASSES)


@pytest.mark.parametrize("event", EVENTS, ids=lambda e: e.TYPE)
def test_event_roundtrip(event: Event) -> None:
    line = encode(to_envelope(event))
    assert line.endswith(b"\n")
    assert parse_event(decode_line(line)) == event


@pytest.mark.parametrize("cmd", COMMANDS, ids=[f"{i}-{c.TYPE}" for i, c in enumerate(COMMANDS)])
def test_command_roundtrip(cmd: Command) -> None:
    line = encode(to_envelope(cmd, "req-1"))
    assert parse_command(decode_line(line)) == ("req-1", cmd)


# ---- encode --------------------------------------------------------------------------------


def test_encode_rejects_oversize() -> None:
    env = Envelope(type="x", data={"b": "a" * MAX_LINE})
    with pytest.raises(ProtocolError) as ei:
        encode(env)
    assert ei.value.code == "line_too_big"


def test_encode_omits_null_request_id_and_is_compact() -> None:
    line = encode(Envelope(type="x"))
    assert json.loads(line) == {"v": 1, "type": "x", "data": {}}
    assert b" " not in line


# ---- sin PII en errores --------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        f'{{"v":1,"type":"t","extra":"{SECRET}"}}\n'.encode(),
        f'{{"v":1,"type":"{SECRET}" "x"}}\n'.encode(),
        f'{{"v":2,"type":"{SECRET}"}}\n'.encode(),
        f'["{SECRET}"]\n'.encode(),
        f'{{"a": {SECRET}}}\n'.encode(),
        f'{{"v":1,"type":"t","request_id":"{SECRET} x"}}\n'.encode(),
        SECRET.encode() + b"\xff\n",
        f'{{"v":1,"type":"t","data":{SECRET}}}\n'.encode(),
    ],
)
def test_decode_errors_do_not_leak_input(line: bytes) -> None:
    with pytest.raises(ProtocolError) as ei:
        decode_line(line)
    assert SECRET not in str(ei.value)
    assert SECRET not in ei.value.message


@pytest.mark.parametrize(
    ("type_", "data"),
    [
        ("send_text", {"chat_jid": JID, "text": "", "extra": SECRET}),
        ("send_text", {"chat_jid": SECRET * 20, "text": ""}),
        ("list_chats", {"limit": SECRET}),
        (SECRET, {}),
    ],
)
def test_parse_errors_do_not_leak_input(type_: str, data: dict[str, Any]) -> None:
    with pytest.raises(ProtocolError) as ei:
        parse_command(_cmd(type_, "r1", data))
    assert SECRET not in str(ei.value)
    assert SECRET[:8] not in ei.value.message
