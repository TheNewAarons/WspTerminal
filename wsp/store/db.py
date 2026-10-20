"""Conexiones SQLite (stdlib, WAL). Solo el daemon abre la conexión de escritura."""

import contextlib
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

from wsp import paths

_FILE_MODE = 0o600
_BUSY_TIMEOUT_MS = 5000
_SUFFIXES = ("", "-wal", "-shm")


def _secure(path: Path) -> None:
    """Fuerza permisos 600 en la DB y en sus archivos auxiliares si existen."""
    for suffix in _SUFFIXES:
        f = Path(str(path) + suffix)
        if f.exists():
            f.chmod(_FILE_MODE)


def _resolve(path: Path | None) -> Path:
    if path is None:
        paths.ensure_dirs()
        return paths.db_path()
    return path


def open_write(path: Path | None = None) -> sqlite3.Connection:
    """Única conexión de escritura (daemon). Modo autocommit; usar `transaction`."""
    target = _resolve(path)
    old_umask = os.umask(0o077)
    try:
        conn = sqlite3.connect(target, isolation_level=None)
    finally:
        os.umask(old_umask)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        _secure(target)
    except BaseException:
        conn.close()
        raise
    return conn


def open_read(path: Path | None = None) -> sqlite3.Connection:
    """Conexión de solo lectura (CLI) vía URI mode=ro."""
    target = _resolve(path)
    uri = "file:" + quote(str(target)) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA query_only = ON")
    except BaseException:
        conn.close()
        raise
    return conn


def secure_files(path: Path | None = None) -> None:
    """Reaplica 600 (útil tras crear -wal/-shm en la primera escritura)."""
    _secure(_resolve(path))


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE (o SAVEPOINT si ya hay transacción); rollback ante excepción."""
    if conn.in_transaction:
        conn.execute("SAVEPOINT wsp_nested")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK TO wsp_nested")
            conn.execute("RELEASE wsp_nested")
            raise
        conn.execute("RELEASE wsp_nested")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
