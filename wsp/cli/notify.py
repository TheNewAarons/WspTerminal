"""Notificaciones de macOS vía `osascript`.

El texto nunca se interpola en el script AppleScript: va como argumentos (`argv`) de un
script fijo, así comillas, backslashes y saltos de línea no pueden inyectar código.
"""

import asyncio
import os
import subprocess
from collections.abc import Callable, Mapping
from typing import Final

from wsp.cli import render

SCRIPT: Final = (
    "on run argv",
    "display notification (item 2 of argv) with title (item 1 of argv)",
    "end run",
)
FRONT_SCRIPT: Final = (
    'tell application "System Events" to get name of first application process '
    "whose frontmost is true"
)
TITLE_MAX: Final = 60
BODY_MAX: Final = 200
OSASCRIPT_TIMEOUT: Final = 3.0
# TERM_PROGRAM → nombre del proceso en System Events.
_TERMINALS: Final = {
    "Apple_Terminal": "Terminal",
    "iTerm.app": "iTerm2",
    "ghostty": "ghostty",
    "WezTerm": "wezterm-gui",
    "vscode": "Code",
    "Hyper": "Hyper",
}

Runner = Callable[[list[str]], str | None]  # argv → stdout, o None si falló


def build_argv(title: str, body: str) -> list[str]:
    """argv de `osascript` con el texto como argumentos (sin interpolar en el script)."""
    argv = ["/usr/bin/osascript"]
    for line in SCRIPT:
        argv += ["-e", line]
    return [*argv, "--", render.truncate(title, TITLE_MAX), render.truncate(body, BODY_MAX)]


def run_osascript(argv: list[str]) -> str | None:
    try:
        r = subprocess.run(  # noqa: S603 - argv fijo, sin shell
            argv, capture_output=True, text=True, check=False, timeout=OSASCRIPT_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def terminal_name(environ: Mapping[str, str]) -> str | None:
    return _TERMINALS.get(environ.get("TERM_PROGRAM", ""))


def terminal_has_focus(
    run: Runner = run_osascript, environ: Mapping[str, str] = os.environ
) -> bool:
    """True solo si sabemos con certeza que la terminal es la app al frente.

    Cualquier duda (terminal desconocida, osascript falla) ⇒ False ⇒ se notifica siempre.
    """
    mine = terminal_name(environ)
    if mine is None:
        return False
    front = run(["/usr/bin/osascript", "-e", FRONT_SCRIPT])
    return front is not None and front == mine


async def notify(
    title: str,
    body: str,
    *,
    run: Runner = run_osascript,
    environ: Mapping[str, str] = os.environ,
) -> bool:
    """Envía la notificación si la terminal no tiene el foco; no bloquea el loop."""
    loop = asyncio.get_running_loop()
    if await loop.run_in_executor(None, lambda: terminal_has_focus(run, environ)):
        return False
    return await loop.run_in_executor(None, lambda: run(build_argv(title, body)) is not None)
