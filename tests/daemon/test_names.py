"""Sincronización de nombres: apply_names, repo.set_chat_title y NameSync."""

import asyncio
import sqlite3

from tests.daemon.fakes import FakeClient
from wsp.daemon.config import ReconnectConfig
from wsp.daemon.names import NameEntry, NameSync, apply_names
from wsp.daemon.session import Session
from wsp.protocol.models import AccountState
from wsp.store import repo
from wsp.store.writer import Writer

DIRECT = "5215512345678@s.whatsapp.net"
GROUP = "120363000000000001@g.us"


def _title(conn: sqlite3.Connection, jid: str) -> str | None:
    row = conn.execute("SELECT title FROM chats WHERE jid = ?", (jid,)).fetchone()
    return None if row is None else row[0]


def _push_name(conn: sqlite3.Connection, jid: str) -> str | None:
    row = conn.execute("SELECT push_name FROM contacts WHERE jid = ?", (jid,)).fetchone()
    return None if row is None else row[0]


def _count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608 - tabla fija
    return int(row[0])


# ---- apply_names --------------------------------------------------------------------------


def test_apply_names_updates_existing_chat_title(conn: sqlite3.Connection) -> None:
    repo.upsert_chat(conn, GROUP, kind="group")
    assert apply_names(conn, [NameEntry(GROUP, "Familia")]) == 1
    assert _title(conn, GROUP) == "Familia"


def test_apply_names_does_not_create_chats(conn: sqlite3.Connection) -> None:
    assert apply_names(conn, [NameEntry(GROUP, "Fantasma")]) == 1
    assert _title(conn, GROUP) is None
    assert _count(conn, "chats") == 0


def test_apply_names_upserts_contact_only_for_direct_jids(conn: sqlite3.Connection) -> None:
    apply_names(conn, [NameEntry(DIRECT, "Juan"), NameEntry(GROUP, "Familia")])
    assert _push_name(conn, DIRECT) == "Juan"
    assert _push_name(conn, GROUP) is None
    assert _count(conn, "contacts") == 1


def test_apply_names_ignores_blank_names(conn: sqlite3.Connection) -> None:
    repo.upsert_chat(conn, DIRECT, kind="direct", title="Previo")
    n = apply_names(conn, [NameEntry(DIRECT, ""), NameEntry(DIRECT, "   \t")])
    assert n == 0
    assert _title(conn, DIRECT) == "Previo"
    assert _count(conn, "contacts") == 0


def test_apply_names_strips_and_truncates_to_256(conn: sqlite3.Connection) -> None:
    repo.upsert_chat(conn, DIRECT, kind="direct")
    apply_names(conn, [NameEntry(DIRECT, "  " + "x" * 300 + "  ")])
    assert _title(conn, DIRECT) == "x" * 256
    assert _push_name(conn, DIRECT) == "x" * 256


# ---- repo.set_chat_title ------------------------------------------------------------------


def test_set_chat_title_false_when_chat_missing(conn: sqlite3.Connection) -> None:
    assert repo.set_chat_title(conn, GROUP, "X") is False
    assert _count(conn, "chats") == 0


def test_set_chat_title_is_idempotent(conn: sqlite3.Connection) -> None:
    repo.upsert_chat(conn, GROUP, kind="group")
    assert repo.set_chat_title(conn, GROUP, "Familia") is True
    assert repo.set_chat_title(conn, GROUP, "Familia") is False  # sin cambios
    assert _title(conn, GROUP) == "Familia"
    assert repo.set_chat_title(conn, GROUP, "Otro") is True
    assert _title(conn, GROUP) == "Otro"


# ---- NameSync -----------------------------------------------------------------------------


async def test_run_once_persists_and_returns_count(
    conn: sqlite3.Connection, writer: Writer
) -> None:
    repo.upsert_chat(conn, GROUP, kind="group")
    client = FakeClient()
    client.names = [NameEntry(GROUP, "Familia"), NameEntry(DIRECT, "Juan"), NameEntry(DIRECT, " ")]
    assert await NameSync(client, writer).run_once() == 2
    assert _title(conn, GROUP) == "Familia"
    assert _push_name(conn, DIRECT) == "Juan"


async def test_run_once_returns_zero_when_fetch_raises(writer: Writer) -> None:
    client = FakeClient()

    async def boom() -> list[NameEntry]:
        raise RuntimeError("secreto")

    client.fetch_names = boom  # type: ignore[method-assign]
    assert await NameSync(client, writer).run_once() == 0


async def test_request_debounces_consecutive_calls(
    conn: sqlite3.Connection, writer: Writer
) -> None:
    repo.upsert_chat(conn, GROUP, kind="group")
    client = FakeClient()
    client.names = [NameEntry(GROUP, "Familia")]
    gate = asyncio.Event()
    sleeps: list[float] = []
    fetches = 0
    orig = client.fetch_names

    async def counting() -> list[NameEntry]:
        nonlocal fetches
        fetches += 1
        return await orig()

    client.fetch_names = counting  # type: ignore[method-assign]

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)
        await gate.wait()

    ns = NameSync(client, writer, delay=0, sleep=fake_sleep)
    ns.request()
    await asyncio.sleep(0)
    ns.request()
    ns.request()
    await asyncio.sleep(0)
    assert fetches == 0  # las anteriores se cancelaron; ninguna llegó a pedir
    gate.set()
    task = ns._task
    assert task is not None
    await asyncio.wait_for(task, 2)
    assert fetches == 1
    assert sleeps and all(d == 0 for d in sleeps)
    assert _title(conn, GROUP) == "Familia"
    await ns.aclose()


async def test_aclose_cancels_pending_cleanly(writer: Writer) -> None:
    client = FakeClient()
    started = asyncio.Event()

    async def hang(_d: float) -> None:
        started.set()
        await asyncio.Event().wait()

    ns = NameSync(client, writer, delay=0, sleep=hang)
    ns.request()
    await started.wait()
    await asyncio.wait_for(ns.aclose(), 2)
    assert ns._task is not None and ns._task.cancelled()
    await ns.aclose()  # idempotente


async def test_aclose_without_request_is_noop(writer: Writer) -> None:
    await NameSync(FakeClient(), writer).aclose()


# ---- Session.on_connected -----------------------------------------------------------------


async def test_session_calls_on_connected_on_connected_event() -> None:
    calls: list[int] = []
    states: list[AccountState] = []

    async def sink(_kind: str, _ev: object) -> None:
        return None

    s = Session(
        FakeClient(),
        ReconnectConfig(base=1.0, cap=10.0, jitter=1.0),
        states.append,
        sink,
        on_connected=lambda: calls.append(1),
    )
    await s.on_event("ConnectedEv", None)
    assert calls == [1]
    await s.on_event("DisconnectedEv", None)
    assert calls == [1]
