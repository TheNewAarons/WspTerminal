"""Fixtures del daemon: WSP_HOME corto, DB migrada, logging restaurado, sockets disponibles."""

import logging
import os
import socket
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from wsp.store import db, migrate


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.open_write(tmp_path / "wsp.db")
    migrate.migrate(c)
    yield c
    c.close()


@pytest.fixture
def restore_process_state() -> Iterator[None]:
    """Restaura umask, handlers del root logger y configuración de structlog."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    umask = os.umask(0o022)
    os.umask(umask)
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
        if h not in handlers:
            h.close()
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level)
    structlog.reset_defaults()
    os.umask(umask)


@pytest.fixture
def unix_sockets(short_path: Path) -> None:
    """Salta el test si el sandbox impide crear sockets Unix."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(str(short_path / "probe.sock"))
    except (PermissionError, OSError) as e:
        pytest.skip(f"sandbox impide sockets Unix: {type(e).__name__}")
    finally:
        s.close()
