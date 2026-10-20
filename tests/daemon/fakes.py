"""Dobles de prueba del daemon: cliente de WhatsApp falso y constructores de eventos neonize."""

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

from wsp.daemon.session import EventSink, QrHandler, SendResult

ns = SimpleNamespace


def jid(user: str, server: str = "s.whatsapp.net") -> SimpleNamespace:
    return ns(User=user, Server=server)


DIRECT = jid("5215511111111")
DIRECT_STR = "5215511111111@s.whatsapp.net"
GROUP = jid("120363000000000001", "g.us")
GROUP_STR = "120363000000000001@g.us"


def message_ev(
    message: Any,
    *,
    chat: SimpleNamespace = DIRECT,
    sender: SimpleNamespace | None = None,
    wa_id: str = "A1",
    from_me: bool = False,
    ts: int = 1_700_000_000,
    pushname: str = "",
) -> SimpleNamespace:
    src = ns(Chat=chat, Sender=sender if sender is not None else chat, IsFromMe=from_me)
    info = ns(MessageSource=src, ID=wa_id, Timestamp=ts, Pushname=pushname)
    return ns(Info=info, Message=message)


def text_msg(text: str) -> SimpleNamespace:
    return ns(conversation=text)


def ext_msg(text: str, stanza: str = "") -> SimpleNamespace:
    return ns(extendedTextMessage=ns(text=text, contextInfo=ns(stanzaID=stanza)))


def proto_msg(type_: int, target: str, edited: Any = None) -> SimpleNamespace:
    proto = ns(type=type_, key=ns(ID=target))
    if edited is not None:
        proto.editedMessage = edited
    return ns(protocolMessage=proto)


def receipt_ev(type_: int, ids: list[str], chat: SimpleNamespace = DIRECT) -> SimpleNamespace:
    return ns(Type=type_, MessageSource=ns(Chat=chat), MessageIDs=ids)


def history_ev(conversations: list[SimpleNamespace]) -> SimpleNamespace:
    return ns(Data=ns(conversations=conversations))


def history_msg(
    wa_id: str, message: Any, *, from_me: bool = False, participant: str = "", ts: int = 1_000
) -> SimpleNamespace:
    key = ns(ID=wa_id, fromMe=from_me, participant=participant)
    return ns(message=ns(key=key, message=message, messageTimestamp=ts))


class FakeClient:
    """WaClient falso: sin red, guionizable, sin sleeps."""

    def __init__(self) -> None:
        self.on_event: EventSink | None = None
        self.on_qr: QrHandler | None = None
        self.connects = 0
        self.disconnects = 0
        self.sent: list[tuple[str, str, str | None, str | None]] = []
        self.reactions: list[tuple[str, str, str | None, str]] = []
        self.connect_script: list[BaseException | None] = []
        self.after_connect: Callable[[FakeClient], Awaitable[None]] | None = None
        self.send_errors: list[BaseException] = []
        self.phone_code = "ABCD-1234"
        self.phones: list[str] = []
        self.disconnect_error: BaseException | None = None
        self.closed: asyncio.Future[None] | None = None
        self._n = 0

    def bind(self, on_event: EventSink, on_qr: QrHandler) -> None:
        self.on_event = on_event
        self.on_qr = on_qr

    async def connect(self) -> None:
        self.connects += 1
        self.closed = asyncio.get_running_loop().create_future()
        if self.connect_script:
            err = self.connect_script.pop(0)
            if err is not None:
                raise err
        if self.after_connect is not None:
            await self.after_connect(self)

    async def wait_closed(self) -> None:
        if self.closed is not None:
            await self.closed

    def close(self) -> None:
        if self.closed is not None and not self.closed.done():
            self.closed.set_result(None)

    async def disconnect(self) -> None:
        self.disconnects += 1
        if self.disconnect_error is not None:
            raise self.disconnect_error

    async def emit(self, kind: str, ev: Any = None) -> None:
        assert self.on_event is not None
        await self.on_event(kind, ev)

    async def send_text(
        self, jid: str, text: str, quoted_wa_id: str | None = None, quoted_sender: str | None = None
    ) -> SendResult:
        self.sent.append((jid, text, quoted_wa_id, quoted_sender))
        if self.send_errors:
            raise self.send_errors.pop(0)
        self._n += 1
        return SendResult(wa_id=f"WA{self._n}", ts=1_700_000_000)

    async def send_reaction(
        self, chat_jid: str, wa_id: str, author_jid: str | None, emoji: str
    ) -> SendResult:
        self.reactions.append((chat_jid, wa_id, author_jid, emoji))
        if self.send_errors:
            raise self.send_errors.pop(0)
        self._n += 1
        return SendResult(wa_id=f"WA{self._n}", ts=1_700_000_000)

    async def pair_phone(self, phone: str) -> str:
        self.phones.append(phone)
        return self.phone_code


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now
