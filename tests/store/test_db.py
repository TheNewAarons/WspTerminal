import sqlite3
import stat
from pathlib import Path

import pytest

from wsp.store import db, migrate, repo


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_permissions_600(conn: sqlite3.Connection, db_path: Path) -> None:
    repo.upsert_message(conn, repo.MessageIn("a@s.whatsapp.net", "1", 1, body="x"))
    db.secure_files(db_path)
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(db_path) + suffix)
        assert f.exists(), suffix
        assert _mode(f) == 0o600, suffix


def test_pragmas(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_foreign_key_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO messages (chat_jid, wa_id, ts) VALUES ('nochat', '1', 1)")


def test_read_connection_rejects_writes(conn: sqlite3.Connection, db_path: Path) -> None:
    repo.upsert_chat(conn, "a@s.whatsapp.net", title="A")
    r = db.open_read(db_path)
    try:
        assert r.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert len(repo.list_chats(r)) == 1
        with pytest.raises(sqlite3.OperationalError):
            r.execute("INSERT INTO meta (key, value) VALUES ('a', 'b')")
        with pytest.raises(sqlite3.OperationalError):
            repo.upsert_chat(r, "b@s.whatsapp.net")
    finally:
        r.close()


def test_transaction_rollback_and_nested(conn: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError), db.transaction(conn):
        conn.execute("INSERT INTO meta (key, value) VALUES ('a', '1')")
        raise RuntimeError("boom")
    assert repo.meta_get(conn, "a") is None
    with db.transaction(conn):
        conn.execute("INSERT INTO meta (key, value) VALUES ('b', '1')")
        with pytest.raises(RuntimeError), db.transaction(conn):
            conn.execute("INSERT INTO meta (key, value) VALUES ('c', '1')")
            raise RuntimeError("inner")
    assert repo.meta_get(conn, "b") == "1"
    assert repo.meta_get(conn, "c") is None


def test_default_path_uses_wsp_home(short_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSP_HOME", str(short_path / "home"))
    c = db.open_write()
    try:
        assert migrate.migrate(c) == migrate.latest_version()
    finally:
        c.close()
    assert any((short_path / "home").rglob("*.db"))


def test_open_write_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.db"
    real.touch()
    link = tmp_path / "link.db"
    link.symlink_to(real)
    with pytest.raises(PermissionError):
        db.open_write(link)
    with pytest.raises(PermissionError):
        db.open_read(link)


def test_open_write_creates_600(tmp_path: Path) -> None:
    p = tmp_path / "new.db"
    c = db.open_write(p)
    c.close()
    assert _mode(p) == 0o600


def test_open_write_rejects_loose_parent(tmp_path: Path) -> None:
    parent = tmp_path / "loose"
    parent.mkdir()
    parent.chmod(0o755)
    with pytest.raises(PermissionError):
        db.open_write(parent / "wsp.db")
    assert not (parent / "wsp.db").exists()


def test_open_read_schema_too_new(conn: sqlite3.Connection, db_path: Path) -> None:
    conn.execute(f"PRAGMA user_version = {migrate.latest_version() + 1}")
    with pytest.raises(migrate.SchemaTooNewError):
        db.open_read(db_path)


def test_secure_delete_on(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1


def test_transaction_rollback_guard_keeps_original_error(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="original"), db.transaction(conn):
        conn.execute("ROLLBACK")
        raise ValueError("original")
    assert not conn.in_transaction
    conn.execute("BEGIN")
    with pytest.raises(ValueError, match="anidado"), db.transaction(conn):
        conn.execute("ROLLBACK")
        raise ValueError("anidado")
