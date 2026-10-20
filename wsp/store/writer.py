"""Hilo dedicado para la conexión de escritura: ninguna llamada SQLite bloquea el event loop.

Decisión: UN `ThreadPoolExecutor(max_workers=1)` dueño de la conexión. La conexión se abre dentro de
ese hilo y conserva `check_same_thread=True`, así sqlite3 verifica el invariante "un solo hilo
toca la conexión" y los trabajos se serializan en orden FIFO (equivale al lock implícito del
único escritor). `asyncio.to_thread` (pool por defecto, N hilos) exigiría `check_same_thread=False`
más un lock explícito para el orden y la atomicidad por operación.

Cancelar el `await` de `run` no interrumpe un trabajo ya iniciado: termina y hace commit/rollback
completo (atomicidad intacta); solo se descarta su resultado.
"""

import asyncio
import contextlib
import functools
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Concatenate

from wsp.store import db


class WriterClosed(RuntimeError):
    """El hilo del writer ya se cerró; no se aceptan más trabajos."""


def _finalize(conn: sqlite3.Connection) -> None:
    with contextlib.suppress(sqlite3.Error):
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


class Writer:
    """Ejecuta funciones `fn(conn, ...)` en el hilo único dueño de la conexión writer."""

    def __init__(self, conn: sqlite3.Connection, pool: ThreadPoolExecutor) -> None:
        self._conn = conn
        self._pool = pool
        self._closed = False

    @classmethod
    async def open(cls, opener: Callable[[], sqlite3.Connection] = db.open_write) -> "Writer":
        """Abre la conexión dentro del hilo dedicado."""
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wsp-db")
        try:
            conn = await asyncio.get_running_loop().run_in_executor(pool, opener)
        except BaseException:
            pool.shutdown(wait=False)
            raise
        return cls(conn, pool)

    @classmethod
    def adopt(cls, conn: sqlite3.Connection) -> "Writer":
        """Envuelve una conexión ya abierta (debe permitir uso desde otro hilo; solo tests)."""
        return cls(conn, ThreadPoolExecutor(max_workers=1, thread_name_prefix="wsp-db"))

    async def run[**P, R](
        self, fn: Callable[Concatenate[sqlite3.Connection, P], R], /, *args: P.args, **kw: P.kwargs
    ) -> R:
        """Ejecuta `fn(conn, *args, **kw)` en el hilo del writer y espera su resultado."""
        if self._closed:
            raise WriterClosed("writer cerrado")
        call = functools.partial(fn, self._conn, *args, **kw)
        try:
            return await asyncio.get_running_loop().run_in_executor(self._pool, call)
        except RuntimeError as e:  # el pool se cerró entre el chequeo y el envío
            if "shutdown" in str(e):
                raise WriterClosed("writer cerrado") from None
            raise

    async def aclose(self) -> None:
        """Checkpoint + cierre de la conexión (tras los trabajos pendientes) y fin del hilo."""
        if self._closed:
            return
        self._closed = True
        try:
            await asyncio.get_running_loop().run_in_executor(
                self._pool, functools.partial(_finalize, self._conn)
            )
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Detiene el hilo (espera el trabajo en curso)."""
        self._closed = True
        self._pool.shutdown(wait=True)
