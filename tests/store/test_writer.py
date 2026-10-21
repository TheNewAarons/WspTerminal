"""Writer: hilo único dueño de la conexión; el loop no se bloquea y el orden es FIFO."""

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from wsp.store import db, migrate
from wsp.store.writer import Writer, WriterClosed


def _thread_name(_conn: sqlite3.Connection) -> str:
    return threading.current_thread().name


async def _open(path: Path) -> Writer:
    def opener() -> sqlite3.Connection:
        c = db.open_write(path)  # check_same_thread=True: el hilo del writer es el dueño
        migrate.migrate(c)
        return c

    return await Writer.open(opener)


async def test_jobs_run_in_dedicated_thread_and_in_order(tmp_path: Path) -> None:
    w = await _open(tmp_path / "w.db")
    try:
        assert (await w.run(_thread_name)).startswith("wsp-db")
        order: list[int] = []

        def job(_c: sqlite3.Connection, i: int) -> None:
            time.sleep(0.01 * (3 - i))
            order.append(i)

        await asyncio.gather(*(w.run(job, i) for i in range(3)))
        assert order == [0, 1, 2]
    finally:
        await w.aclose()


async def test_event_loop_is_not_blocked_by_slow_job(tmp_path: Path) -> None:
    w = await _open(tmp_path / "w.db")
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    t = asyncio.create_task(ticker())
    try:
        await w.run(lambda _c: time.sleep(0.15))
        assert ticks >= 10
    finally:
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
        await w.aclose()


async def test_cancelled_caller_does_not_interrupt_started_job(tmp_path: Path) -> None:
    w = await _open(tmp_path / "w.db")
    started, done = threading.Event(), threading.Event()

    def job(c: sqlite3.Connection) -> None:
        started.set()
        time.sleep(0.05)
        c.execute("INSERT INTO meta(key, value) VALUES ('k', 'v')")
        done.set()

    task = asyncio.create_task(w.run(job))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await w.aclose()  # espera al trabajo pendiente antes de cerrar
    assert done.is_set()


async def test_close_checkpoints_closes_conn_and_rejects_new_jobs(tmp_path: Path) -> None:
    w = await _open(tmp_path / "w.db")
    conns: list[sqlite3.Connection] = []
    await w.run(conns.append)
    await w.aclose()
    await w.aclose()  # idempotente
    with pytest.raises(WriterClosed):
        await w.run(_thread_name)
    with pytest.raises(sqlite3.ProgrammingError):
        conns[0].cursor()
