"""Fixtures globales."""

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def short_path() -> Iterator[Path]:
    """Directorio temporal corto: el tmp_path de pytest en macOS rebasa el límite AF_UNIX."""
    d = Path(tempfile.mkdtemp(prefix="wsp-")).resolve()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
