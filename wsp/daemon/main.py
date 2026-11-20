"""Entrypoint de `wspd`: único proceso con la sesión de WhatsApp."""

import asyncio
import contextlib
import logging
import os
import signal
import sqlite3
import sys
from collections.abc import Callable
from typing import Any, Final

from wsp import __version__, paths
from wsp.daemon.config import Config, ConfigError, load_config
from wsp.daemon.events import Bridge
from wsp.daemon.logsetup import configure_logging
from wsp.daemon.outbox import Commands, Outbox
from wsp.daemon.session import (
    EXIT_ALREADY_RUNNING,
    AlreadyRunningError,
    InstanceLock,
    NeonizeClient,
    Session,
    WaClient,
)
from wsp.protocol.models import Event
from wsp.protocol.server import Server, sqlite_backfill
from wsp.store import db, migrate, repo
from wsp.store.writer import Writer

log = logging.getLogger(__name__)

LOG_NAME: Final = "wspd.log"
MAINTENANCE_INTERVAL: Final = 24 * 3600.0
SHUTDOWN_TIMEOUT: Final = 10.0
_STOP_SIGNALS: Final = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)

ClientFactory = Callable[[], WaClient]


def _maintenance_once(conn: sqlite3.Connection, retention_days: int) -> None:
    """Hilo del writer: purga + optimize + checkpoint + permisos."""
    result = repo.purge_older_than(conn, retention_days)
    repo.fts_optimize(conn)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.secure_files()
    log.info(
        "mantenimiento messages=%d reactions=%d outbox=%d chats=%d contacts=%d",
        result.messages,
        result.reactions,
        result.outbox,
        result.chats,
        result.contacts,
    )


async def _maintenance(writer: Writer, retention_days: int) -> None:
    while True:
        try:
            await writer.run(_maintenance_once, retention_days)
        except sqlite3.Error as e:
            log.error("mantenimiento falló exc=%s", type(e).__name__)
        await asyncio.sleep(MAINTENANCE_INTERVAL)


async def _cancel(*tasks: asyncio.Task[Any] | None) -> None:
    """Cancela y espera: ninguna tarea queda huérfana ni se destruye pendiente."""
    live = [t for t in tasks if t is not None]
    for t in live:
        t.cancel()
    if live:
        await asyncio.gather(*live, return_exceptions=True)


async def serve(
    cfg: Config,
    client_factory: ClientFactory = NeonizeClient,
    stop: asyncio.Event | None = None,
) -> None:
    """Levanta DB, socket, outbox y sesión; al recibir `stop` (o logged_out) cierra limpio.

    Apagado (orden): señales → mantenimiento → drenar outbox (sesión viva) → desconectar sesión →
    socket (cancela handlers/escritores) → encolados en vuelo → cerrar el hilo del writer.
    """
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in _STOP_SIGNALS:
        loop.add_signal_handler(sig, stop.set)

    writer = await Writer.open(db.open_write)
    server: Server | None = None
    session_task: asyncio.Task[None] | None = None
    maint_task: asyncio.Task[None] | None = None
    stopper: asyncio.Task[bool] | None = None
    outbox: Outbox | None = None
    session: Session | None = None
    try:
        await writer.run(migrate.migrate)
        recovered = await writer.run(repo.outbox_recover)
        if recovered:
            log.warning("outbox: envíos interrumpidos n=%d", recovered)

        def emit(event: Event) -> None:
            if server is not None:
                server.broadcast(event)

        client = client_factory()
        bridge = Bridge(writer, emit)
        session = Session(client, cfg.reconnect, emit, bridge.handle)
        outbox = Outbox(writer, client, cfg.outbox, emit)
        server = Server(paths.socket_path(), Commands(writer, outbox), sqlite_backfill())
        await server.start()
        outbox.start()
        maint_task = asyncio.create_task(
            _maintenance(writer, cfg.retention_days), name="maintenance"
        )
        session_task = asyncio.create_task(session.run(), name="session")
        log.info("wspd %s listo", __version__)

        stopper = asyncio.create_task(stop.wait(), name="stop-wait")
        await asyncio.wait({stopper, session_task}, return_when=asyncio.FIRST_COMPLETED)
        if session_task.done() and not stop.is_set():
            log.warning("sesión terminada state=%s", session.state)
    finally:
        log.info("apagando")
        for sig in _STOP_SIGNALS:
            loop.remove_signal_handler(sig)
        try:
            await _cancel(stopper, maint_task)
            # Drenar outbox con la sesión viva; luego desconectar y cerrar el socket.
            if outbox is not None:
                with contextlib.suppress(Exception):
                    await outbox.stop(SHUTDOWN_TIMEOUT)
            if session is not None:
                with contextlib.suppress(Exception):
                    await session.stop()
            await _cancel(session_task)
            if server is not None:
                with contextlib.suppress(Exception):
                    await server.stop()
            if outbox is not None:
                await outbox.join()
        finally:
            await writer.aclose()


def main() -> int:
    """Devuelve el código de salida; 2 si ya hay otro wspd."""
    os.umask(0o077)
    paths.ensure_dirs()
    try:
        cfg = load_config()
    except ConfigError as e:
        sys.stderr.write(f"wspd: {e}\n")
        return 1
    lock = InstanceLock()
    try:
        lock.acquire()
    except AlreadyRunningError as e:
        sys.stderr.write(f"wspd: {e}\n")
        return EXIT_ALREADY_RUNNING
    try:
        configure_logging(paths.state_dir() / LOG_NAME, cfg.log.level)
        asyncio.run(serve(cfg))
    finally:
        lock.release()
    return 0


def run() -> None:
    """Punto de entrada del daemon (`wspd`)."""
    sys.exit(main())
