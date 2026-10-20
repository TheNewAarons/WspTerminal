"""Fixtures globales."""

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from wsp import paths


@pytest.fixture
def short_path() -> Iterator[Path]:
    """Directorio temporal corto: el tmp_path de pytest en macOS rebasa el límite AF_UNIX."""
    d = Path(tempfile.mkdtemp(prefix="wsp-")).resolve()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def wsp_home(short_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """WSP_HOME en ruta corta (el socket AF_UNIX no admite rutas largas) con dirs creados."""
    home = short_path / "h"
    monkeypatch.setenv("WSP_HOME", str(home))
    paths.ensure_dirs()
    return home
