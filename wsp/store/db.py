"""Conexiones SQLite (stdlib, WAL). Solo el daemon abre la conexión de escritura."""

import contextlib
import os
import sqlite3
import stat
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

from wsp import paths
from wsp.store.migrate import SchemaTooNewError, current_version, latest_version

_FILE_MODE = 0o600
_DIR_MODE = 0o700
_SUFFIXES = ("", "-wal", "-shm")


def _secure(path: Path) -> None:
    """Fuerza permisos 600 en la DB y auxiliares; rechaza symlinks; sin seguir enlaces."""
    for suffix in _SUFFIXES:
        f = Path(str(path) + suffix)
        try:
            mode = f.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise PermissionError(f"{f.name} no puede ser un symlink")
        if os.chmod in os.supports_follow_symlinks:
            os.chmod(f, _FILE_MODE, follow_symlinks=False)
        else:  # pragma: no cover - plataformas sin lchmod; ya se descartó el symlink
            os.chmod(f, _FILE_MODE)


def _check_target(target: Path) -> None:
    """Rechaza symlinks y un directorio padre ajeno o con permisos distintos de 700."""
    if target.is_symlink():
        raise PermissionError("la ruta de la DB no puede ser un symlink")
    parent = target.parent
    if parent.exists():
        st = parent.stat()
        if st.st_uid != os.getuid():
            raise PermissionError("el directorio de la DB pertenece a otro usuario")
        if stat.S_IMODE(st.st_mode) != _DIR_MODE:
            raise PermissionError("el directorio de la DB debe tener permisos 700")


def _resolve(path: Path | None) -> Path:
    if path is None:
        paths.ensure_dirs()
        target = paths.db_path()
    else:
        target = path
    _check_target(target)
    return target


def _create_private(target: Path) -> None:
    """Crea el archivo con 600 desde el inicio (no depende del umask global)."""
    fd = os.open(target, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, _FILE_MODE)
    os.close(fd)


def open_write(path: Path | None = None) -> sqlite3.Connection:
    """Única conexión de escritura (daemon). Modo autocommit; usar `transaction`."""
    target = _resolve(path)
    _create_private(target)
    conn = sqlite3.connect(target, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA secure_delete = ON")
        _secure(target)
    except BaseException:
        conn.close()
        raise
    return conn


def open_read(path: Path | None = None) -> sqlite3.Connection:
    """Conexión de solo lectura (CLI) vía URI mode=ro; falla si el esquema es más nuevo."""
    target = _resolve(path)
    uri = "file:" + quote(str(target)) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA query_only = ON")
        version = current_version(conn)
        if version > latest_version():
            raise SchemaTooNewError(
                f"la DB está en versión {version}; actualiza wsp (conoce hasta {latest_version()})"
            )
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


def vacuum(conn: sqlite3.Connection) -> None:
    """VACUUM opcional (el daemon lo programa periódicamente, p. ej. semanal/tras purgas grandes).

    Reconstruye el archivo y elimina páginas libres con datos residuales. Requiere sin transacción.
    """
    if conn.in_transaction:
        raise RuntimeError("vacuum requiere una conexión sin transacción abierta")
    conn.execute("VACUUM")
