"""`wsp daemon logs`: últimas líneas del log JSON del daemon y seguimiento (tail -f)."""

import json
import os
import re
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Final, TextIO

from wsp import paths
from wsp.daemon.logsetup import LOG_BACKUPS
from wsp.daemon.main import LOG_NAME

POLL_SECONDS: Final = 0.25
_CTRL_RE: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_COLORS: Final = {"error": "31", "critical": "31", "warning": "33", "debug": "2"}
_RESERVED: Final = frozenset({"event", "level", "timestamp", "logger"})


def log_path() -> Path:
    return paths.state_dir() / LOG_NAME


def _clean(text: str) -> str:
    """Quita caracteres de control: el log no debe poder manipular la terminal."""
    return _CTRL_RE.sub("", text)


def format_line(raw: str, color: bool = False) -> str:
    """`14:02:11 INFO  wsp.daemon.main  mensaje k=v`; líneas no JSON se devuelven limpias."""
    try:
        data = json.loads(raw)
    except ValueError:
        return _clean(raw)
    if not isinstance(data, dict):
        return _clean(raw)
    try:
        when = datetime.fromisoformat(str(data["timestamp"])).astimezone().strftime("%H:%M:%S")
    except (KeyError, ValueError):
        when = "--:--:--"
    level = str(data.get("level", "info"))
    head = f"{when} {_clean(level.upper()):<7}"
    extra = " ".join(f"{k}={v}" for k, v in data.items() if k not in _RESERVED)
    parts = [str(data.get("logger", "")), str(data.get("event", "")), extra]
    body = _clean("  ".join(p for p in parts if p))
    if color and (code := _COLORS.get(level)):
        return f"\x1b[{code}m{head}\x1b[0m  {body}"
    return f"{head}  {body}"


def _files_newest_first(path: Path) -> list[Path]:
    return [path, *(path.with_name(f"{path.name}.{i}") for i in range(1, LOG_BACKUPS + 1))]


def last_lines(path: Path, n: int) -> list[str]:
    """Últimas `n` líneas, incluyendo los archivos rotados si hace falta."""
    out: deque[str] = deque()
    for f in _files_newest_first(path):
        if len(out) >= n:
            break
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                lines = deque(fh, maxlen=n - len(out))
        except FileNotFoundError:
            continue
        out.extendleft(reversed([ln.rstrip("\n") for ln in lines]))
    return list(out)


def follow(
    path: Path,
    emit: Callable[[str], None],
    *,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Sigue el archivo desde su final; si rota (inodo nuevo) o se trunca, relee desde 0."""
    fh: TextIO | None = None
    ino = -1
    first = True  # solo el primer intento arranca en el final; los reabiertos leen desde 0
    partial = ""
    try:
        while not should_stop():
            try:
                st: os.stat_result | None = os.stat(path)
            except FileNotFoundError:
                st = None
            if st is not None and st.st_ino != ino:
                if fh is not None:
                    fh.close()
                fh = path.open(encoding="utf-8", errors="replace")
                ino = st.st_ino
                partial = ""
                if first and st.st_size:
                    fh.seek(0, os.SEEK_END)
            first = False
            if fh is not None:
                if st is not None and st.st_size < fh.tell():
                    fh.seek(0)
                    partial = ""
                while chunk := fh.readline():
                    partial += chunk
                    if partial.endswith("\n"):
                        emit(partial.rstrip("\n"))
                        partial = ""
            sleep(POLL_SECONDS)
    finally:
        if fh is not None:
            fh.close()
