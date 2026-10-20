import sqlite3
from pathlib import Path

import pytest

from wsp.store import db, migrate


def test_from_scratch_reaches_latest(db_path: Path) -> None:
    c = db.open_write(db_path)
    try:
        n = len(migrate.load_migrations())
        assert n >= 2
        assert migrate.migrate(c) == n
        assert migrate.current_version(c) == n == migrate.latest_version()
    finally:
        c.close()


def test_rerun_is_noop(conn: sqlite3.Connection) -> None:
    before = migrate.current_version(conn)
    conn.execute("INSERT INTO meta (key, value) VALUES ('k', 'v')")
    assert migrate.migrate(conn) == before
    assert conn.execute("SELECT value FROM meta WHERE key = 'k'").fetchone()[0] == "v"


def test_upgrade_from_v1(db_path: Path) -> None:
    c = db.open_write(db_path)
    try:
        first = migrate.load_migrations()[0]
        for stmt in migrate._statements(first.sql):
            c.execute(stmt)
        c.execute("PRAGMA user_version = 1")
        assert migrate.current_version(c) == 1
        assert migrate.migrate(c) == migrate.latest_version()
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        assert "messages_fts" in names
        assert "idx_messages_ts" in names
    finally:
        c.close()


def test_schema_too_new(db_path: Path) -> None:
    c = db.open_write(db_path)
    try:
        c.execute("PRAGMA user_version = " + str(migrate.latest_version() + 1))
        with pytest.raises(migrate.SchemaTooNewError):
            migrate.migrate(c)
    finally:
        c.close()


def test_failed_migration_rolls_back(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = migrate.load_migrations()
    bad = migrate.Migration(
        len(real) + 1,
        f"{len(real) + 1:04d}_bad.sql",
        "CREATE TABLE tmp_ok (x INTEGER);\nINSERT INTO no_such_table VALUES (1);\n",
    )
    c = db.open_write(db_path)
    try:
        migrate.migrate(c)
        base = migrate.current_version(c)
        monkeypatch.setattr(migrate, "load_migrations", lambda: [*real, bad])
        with pytest.raises(migrate.MigrationError):
            migrate.migrate(c)
        assert migrate.current_version(c) == base
        assert not c.in_transaction
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        assert "tmp_ok" not in names
    finally:
        c.close()


def test_incomplete_statement_rejected() -> None:
    with pytest.raises(migrate.MigrationError):
        migrate._statements("CREATE TABLE x (a INTEGER")


def test_migrate_refuses_open_transaction(db_path: Path) -> None:
    c = db.open_write(db_path)
    try:
        c.execute("BEGIN")
        with pytest.raises(migrate.MigrationError):
            migrate.migrate(c)
        c.execute("ROLLBACK")
    finally:
        c.close()
