"""Fixtures del store: DB temporal migrada, nunca la real."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from wsp.store import db, migrate


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "wsp.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.open_write(db_path)
    migrate.migrate(c)
    yield c
    c.close()
