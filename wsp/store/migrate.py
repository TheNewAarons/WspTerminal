"""Aplicador de migraciones SQL numeradas, controlado por PRAGMA user_version."""

import re
import sqlite3
from dataclasses import dataclass
from importlib import resources

_NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    """Fallo al aplicar o descubrir migraciones."""


class SchemaTooNewError(MigrationError):
    """La DB tiene un user_version mayor que la última migración del código."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str


def load_migrations() -> list[Migration]:
    """Lee las migraciones empaquetadas, validando numeración contigua desde 0001."""
    found: list[Migration] = []
    for entry in resources.files("wsp.store").joinpath("migrations").iterdir():
        match = _NAME_RE.match(entry.name)
        if match:
            found.append(Migration(int(match.group(1)), entry.name, entry.read_text("utf-8")))
    found.sort(key=lambda m: m.version)
    for expected, mig in enumerate(found, start=1):
        if mig.version != expected:
            raise MigrationError("numeración de migraciones no contigua")
    return found


def latest_version() -> int:
    return len(load_migrations())


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def _statements(sql: str) -> list[str]:
    """Divide un script en sentencias completas (respeta triggers)."""
    out: list[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            out.append(buf)
            buf = ""
    if buf.strip() and not all(
        ln.strip().startswith("--") or not ln.strip() for ln in buf.splitlines()
    ):
        raise MigrationError("sentencia incompleta en migración")
    return out


def migrate(conn: sqlite3.Connection) -> int:
    """Aplica migraciones pendientes; devuelve la versión final. Re-ejecución = no-op."""
    migrations = load_migrations()
    target = len(migrations)
    version = current_version(conn)
    if version > target:
        raise SchemaTooNewError(
            f"la DB está en versión {version} y el código solo conoce hasta {target}"
        )
    if conn.in_transaction:
        raise MigrationError("migrate requiere una conexión sin transacción abierta")
    for mig in migrations[version:]:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Revalidar bajo lock por si otro proceso migró entretanto.
            if current_version(conn) >= mig.version:
                conn.execute("ROLLBACK")
                continue
            for stmt in _statements(mig.sql):
                conn.execute(stmt)
            # PRAGMA no admite parámetros; mig.version es un int derivado del nombre.
            conn.execute("PRAGMA user_version = " + str(int(mig.version)))
            conn.execute("COMMIT")
        except BaseException as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            if isinstance(exc, sqlite3.Error):
                raise MigrationError(f"falló la migración {mig.name}") from exc
            raise
    return current_version(conn)
