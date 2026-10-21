"""LaunchAgent de `wspd`: generación del plist y envoltorio mínimo de `launchctl`."""

import os
import plistlib
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from wsp import paths

LABEL: Final = "cl.aaron.wspd"
THROTTLE_SECONDS: Final = 10
LAUNCHCTL: Final = "/bin/launchctl"
MINIMAL_PATH: Final = "/usr/bin:/bin:/usr/sbin:/sbin"
STDOUT_NAME: Final = "wspd.stdout.log"
STDERR_NAME: Final = "wspd.stderr.log"
_PID_RE: Final = re.compile(r"^\s*pid = (\d+)\s*$", re.MULTILINE)
_STATE_RE: Final = re.compile(r"^\s*state = (\S+)\s*$", re.MULTILINE)

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


class WspdNotFoundError(Exception):
    """`wspd` no está en el PATH."""


@dataclass(frozen=True)
class LaunchdInfo:
    loaded: bool
    pid: int | None = None
    state: str | None = None


def run_launchctl(args: list[str]) -> "subprocess.CompletedProcess[str]":
    """Ejecuta `launchctl` (ruta absoluta, sin shell)."""
    return subprocess.run(  # noqa: S603
        [LAUNCHCTL, *args], capture_output=True, text=True, check=False, timeout=30
    )


def domain() -> str:
    return f"gui/{os.getuid()}"


def service_target() -> str:
    return f"{domain()}/{LABEL}"


def plist_path() -> Path:
    return paths.launch_agents_dir() / f"{LABEL}.plist"


def resolve_wspd() -> Path:
    """Ruta absoluta del binario `wspd`; falla con instrucciones si no está en el PATH."""
    found = shutil.which("wspd")
    if found is None:
        raise WspdNotFoundError(
            "no se encontró `wspd` en el PATH. Instala el paquete (`uv tool install .` "
            "desde el repo) o activa su entorno (`uv sync` y `source .venv/bin/activate`) "
            "y repite `wsp daemon install`."
        )
    return Path(found).absolute()


def build_plist(wspd: Path) -> dict[str, Any]:
    """Contenido del plist. Sin secretos; PATH mínimo; WSP_HOME solo si está definido."""
    env = {"PATH": MINIMAL_PATH}
    if home := os.environ.get("WSP_HOME"):
        env["WSP_HOME"] = home
    state = paths.state_dir()
    return {
        "Label": LABEL,
        "ProgramArguments": [str(wspd)],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": THROTTLE_SECONDS,
        "StandardOutPath": str(state / STDOUT_NAME),
        "StandardErrorPath": str(state / STDERR_NAME),
        "EnvironmentVariables": env,
    }


def _touch_private(path: Path) -> None:
    """Crea el archivo de salida en 600 (el stderr de whatsmeow queda fuera de redact)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def write_plist(wspd: Path) -> Path:
    """Escribe el plist de forma atómica (600) y prepara los archivos de salida."""
    paths.ensure_dirs()
    for name in (STDOUT_NAME, STDERR_NAME):
        _touch_private(paths.state_dir() / name)
    target = plist_path()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if target.is_symlink():
        raise PermissionError("el plist no puede ser un symlink")
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        plistlib.dump(build_plist(wspd), f)
    tmp.replace(target)
    return target


def inspect_service(run: Runner = run_launchctl) -> LaunchdInfo:
    """Estado del servicio según `launchctl print`."""
    r = run(["print", service_target()])
    if r.returncode != 0:
        return LaunchdInfo(loaded=False)
    pid = _PID_RE.search(r.stdout)
    state = _STATE_RE.search(r.stdout)
    return LaunchdInfo(
        loaded=True,
        pid=int(pid.group(1)) if pid else None,
        state=state.group(1) if state else None,
    )


def bootstrap(plist: Path, run: Runner = run_launchctl) -> "subprocess.CompletedProcess[str]":
    return run(["bootstrap", domain(), str(plist)])


def bootout(run: Runner = run_launchctl) -> "subprocess.CompletedProcess[str]":
    return run(["bootout", service_target()])


def kickstart(run: Runner = run_launchctl) -> "subprocess.CompletedProcess[str]":
    return run(["kickstart", "-k", service_target()])
