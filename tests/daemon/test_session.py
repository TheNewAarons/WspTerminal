"""InstanceLock, Backoff, archivos de sesión y supervisión (Session)."""

import asyncio
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from tests.daemon.fakes import FakeClient
from wsp.daemon.config import ReconnectConfig
from wsp.daemon.session import (
    AlreadyRunningError,
    Backoff,
    InstanceLock,
    Session,
    prepare_session_file,
    secure_session_files,
)
from wsp.protocol.models import AccountState

CFG = ReconnectConfig(base=1.0, cap=10.0, jitter=0.5)


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


# ---- InstanceLock -------------------------------------------------------------------------


def test_lock_blocks_second_and_release_allows_reacquire(short_path: Path) -> None:
    path = short_path / "l.lock"
    a, b = InstanceLock(path), InstanceLock(path)
    a.acquire()
    try:
        assert mode(path) == 0o600
        with pytest.raises(AlreadyRunningError):
            b.acquire()
    finally:
        a.release()
    b.acquire()
    b.release()
    b.release()  # idempotente


def test_lock_context_manager(short_path: Path) -> None:
    path = short_path / "l.lock"
    with InstanceLock(path):
        with pytest.raises(AlreadyRunningError):
            InstanceLock(path).acquire()
    with InstanceLock(path):
        pass


def test_lock_refuses_symlink(short_path: Path) -> None:
    target = short_path / "t"
    target.write_text("")
    link = short_path / "l.lock"
    link.symlink_to(target)
    with pytest.raises(OSError):  # noqa: PT011
        InstanceLock(link).acquire()


def test_lock_default_path_uses_state_dir(wsp_home: Path) -> None:
    with InstanceLock():
        assert (wsp_home / "state" / "wspd.lock").exists()


# ---- archivos de sesión -------------------------------------------------------------------


def test_secure_session_files_sets_600(short_path: Path) -> None:
    base = short_path / "session.db"
    for suffix in ("", "-wal", "-shm", "-journal"):
        f = Path(f"{base}{suffix}")
        f.write_text("x")
        f.chmod(0o644)
    secure_session_files(base)
    assert all(mode(Path(f"{base}{s}")) == 0o600 for s in ("", "-wal", "-shm", "-journal"))


def test_secure_session_files_missing_is_ok(short_path: Path) -> None:
    secure_session_files(short_path / "nope.db")


def test_prepare_session_file_creates_600_and_fixes_existing(short_path: Path) -> None:
    target = short_path / "session.db"
    assert prepare_session_file(target) == target
    assert mode(target) == 0o600
    target.chmod(0o666)
    prepare_session_file(target)
    assert mode(target) == 0o600


def test_prepare_session_file_ignores_umask(short_path: Path) -> None:
    old = os.umask(0)
    try:
        target = prepare_session_file(short_path / "s.db")
    finally:
        os.umask(old)
    assert mode(target) == 0o600


# ---- Backoff ------------------------------------------------------------------------------


def test_backoff_exponential_capped_and_reset() -> None:
    b = Backoff(CFG, rand=lambda: 0.5)  # spread = 1
    assert [b.next() for _ in range(6)] == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]
    b.reset()
    assert b.next() == 1.0


def test_backoff_jitter_bounds() -> None:
    assert Backoff(CFG, rand=lambda: 0.0).next() == pytest.approx(0.5)
    assert Backoff(CFG, rand=lambda: 1.0).next() == pytest.approx(1.5)


def test_backoff_never_negative_and_survives_many_attempts() -> None:
    b = Backoff(ReconnectConfig(base=1.0, cap=5.0, jitter=1.0), rand=lambda: 0.0)
    assert all(b.next() >= 0 for _ in range(100))


# ---- Session ------------------------------------------------------------------------------


class Rig:
    def __init__(self, session_file: Path | None = None) -> None:
        self.client = FakeClient()
        self.states: list[AccountState] = []
        self.sunk: list[tuple[str, Any]] = []
        self.delays: list[float] = []
        self.on_sleep: Any = None
        self.session = Session(
            self.client,
            CFG,
            self.states.append,
            self.sink,
            rand=lambda: 0.5,
            sleep=self.sleep,
            session_file=session_file,
        )

    async def sink(self, kind: str, ev: Any) -> None:
        self.sunk.append((kind, ev))

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        if self.on_sleep is not None:
            await self.on_sleep(len(self.delays))

    def labels(self) -> list[tuple[str, str | None]]:
        return [(s.state, s.reason) for s in self.states]


async def test_run_reconnects_after_close_and_connect_error(short_path: Path) -> None:
    r = Rig(short_path / "s.db")
    r.client.connect_script = [None, RuntimeError("secreto"), None]

    async def after(c: FakeClient) -> None:
        if c.connects != 2:  # 1.ª y 3.ª conexión: se cierra de inmediato
            c.close()

    r.client.after_connect = after

    async def on_sleep(n: int) -> None:
        if n == 3:
            await r.session.stop()

    r.on_sleep = on_sleep
    await asyncio.wait_for(r.session.run(), 2)
    assert r.client.connects == 3
    assert r.delays == [1.0, 2.0, 4.0]
    assert r.labels() == [
        ("disconnected", "closed"),
        ("disconnected", "connect_failed"),
        ("disconnected", "closed"),
    ]
    assert r.client.disconnects == 1


async def test_state_only_emitted_on_change() -> None:
    r = Rig()
    await r.session.on_event("ConnectedEv", None)
    await r.session.on_event("ConnectedEv", None)
    await r.session.on_event("DisconnectedEv", None)
    await r.session.on_event("DisconnectedEv", None)
    await r.session.on_event("ConnectedEv", None)
    assert r.labels() == [("connected", None), ("disconnected", "closed"), ("connected", None)]
    assert r.session.state == "connected"


async def test_connection_failure_events_map_to_reasons() -> None:
    r = Rig()
    await r.session.on_event("TemporaryBanEv", None)
    await r.session.on_event("StreamReplacedEv", None)
    assert r.labels() == [("disconnected", "temporary_ban"), ("disconnected", "stream_replaced")]


async def test_other_events_go_to_sink_and_sink_errors_are_swallowed() -> None:
    r = Rig()
    await r.session.on_event("MessageEv", 1)
    assert r.sunk == [("MessageEv", 1)]

    async def bad(_k: str, _e: Any) -> None:
        raise RuntimeError("boom")

    s = Session(r.client, CFG, r.states.append, bad)
    await s.on_event("MessageEv", 1)  # no lanza


async def test_qr_sets_pairing() -> None:
    r = Rig()
    assert r.client.on_qr is not None
    await r.client.on_qr(b"qr")
    await r.client.on_qr(b"qr")
    assert r.labels() == [("pairing", None)]


async def test_logged_out_stops_supervision() -> None:
    r = Rig()
    task = asyncio.create_task(r.session.run())
    await _until(lambda: r.client.connects == 1)
    await r.session.on_event("LoggedOutEv", None)
    r.client.close()
    await asyncio.wait_for(task, 2)
    assert r.client.connects == 1
    assert r.session.state == "logged_out"
    assert r.delays == []


async def test_logged_out_interrupts_backoff_sleep() -> None:
    r = Rig()
    gate = asyncio.Event()

    async def blocked(delay: float) -> None:
        r.delays.append(delay)
        await gate.wait()

    r.session._sleep = blocked
    r.client.after_connect = lambda c: _close(c)
    task = asyncio.create_task(r.session.run())
    await _until(lambda: len(r.delays) == 1)
    await r.session.on_event("LoggedOutEv", None)
    await asyncio.wait_for(task, 2)
    assert r.client.connects == 1


async def test_stop_cuts_sleep() -> None:
    r = Rig()
    gate = asyncio.Event()

    async def blocked(delay: float) -> None:
        r.delays.append(delay)
        await gate.wait()

    r.session._sleep = blocked
    r.client.after_connect = lambda c: _close(c)
    task = asyncio.create_task(r.session.run())
    await _until(lambda: len(r.delays) == 1)
    await r.session.stop()
    await asyncio.wait_for(task, 2)
    assert r.client.disconnects == 1


async def test_stop_swallows_disconnect_error() -> None:
    r = Rig()
    r.client.disconnect_error = RuntimeError("x")
    await r.session.stop()


async def test_connected_event_resets_backoff() -> None:
    r = Rig()
    r.client.connect_script = [RuntimeError("a"), None, None]

    async def after(c: FakeClient) -> None:
        if c.connects == 2:
            await c.emit("ConnectedEv")
        c.close()

    r.client.after_connect = after

    async def on_sleep(n: int) -> None:
        if n == 3:
            await r.session.stop()

    r.on_sleep = on_sleep
    await asyncio.wait_for(r.session.run(), 2)
    assert r.delays == [1.0, 1.0, 2.0]  # el ConnectedEv de la 2.ª conexión reinició el backoff


async def _close(c: FakeClient) -> None:
    c.close()


async def _until(pred: Any) -> None:
    async with asyncio.timeout(2):
        while not pred():  # noqa: ASYNC110
            await asyncio.sleep(0)
