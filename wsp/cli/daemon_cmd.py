"""`wsp daemon install|uninstall|status|logs|restart`."""

import asyncio
import contextlib
import os
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Final, TextIO

from wsp import paths
from wsp.cli import daemon_logs, launchd
from wsp.cli.launchd import Runner, WspdNotFoundError
from wsp.protocol.client import Client, RemoteError, RequestTimeout
from wsp.protocol.models import AccountState, Ping

EXIT_OK: Final = 0
EXIT_ERROR: Final = 1
EXIT_NOT_LOADED: Final = 1
EXIT_UNREACHABLE: Final = 2
EXIT_ACCOUNT: Final = 3
PING_TIMEOUT: Final = 3.0
_ETIME_RE: Final = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")

PsRunner = Callable[[int], str]


def _err(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def _detail(r: "subprocess.CompletedProcess[str]") -> str:
    return (r.stderr or r.stdout or f"código {r.returncode}").strip()


def install(run: Runner = launchd.run_launchctl, out: TextIO = sys.stdout) -> int:
    try:
        wspd = launchd.resolve_wspd()
    except WspdNotFoundError as e:
        _err(f"wsp: {e}")
        return EXIT_ERROR
    try:
        plist = launchd.write_plist(wspd)
    except OSError as e:
        _err(f"wsp: no se pudo escribir el plist: {type(e).__name__}")
        return EXIT_ERROR
    if launchd.inspect_service(run).loaded:  # idempotente: reemplaza la carga previa
        launchd.bootout(run)
    r = launchd.bootstrap(plist, run)
    if r.returncode != 0:
        _err(f"wsp: launchctl bootstrap falló: {_detail(r)}")
        return EXIT_ERROR
    out.write(f"Instalado {launchd.LABEL} ({plist})\n")
    return EXIT_OK


def uninstall(run: Runner = launchd.run_launchctl, out: TextIO = sys.stdout) -> int:
    if launchd.inspect_service(run).loaded:
        r = launchd.bootout(run)
        if r.returncode != 0:
            _err(f"wsp: launchctl bootout falló: {_detail(r)}")
            return EXIT_ERROR
    launchd.plist_path().unlink(missing_ok=True)
    out.write("Desinstalado. Datos y sesión intactos.\n")
    return EXIT_OK


def restart(run: Runner = launchd.run_launchctl, out: TextIO = sys.stdout) -> int:
    if not launchd.inspect_service(run).loaded:
        _err("wsp: el daemon no está instalado. Ejecuta `wsp daemon install`.")
        return EXIT_NOT_LOADED
    r = launchd.kickstart(run)
    if r.returncode != 0:
        _err(f"wsp: launchctl kickstart falló: {_detail(r)}")
        return EXIT_ERROR
    out.write("Daemon reiniciado.\n")
    return EXIT_OK


def parse_etime(text: str) -> int | None:
    """`[[dd-]hh:]mm:ss` (formato de `ps -o etime=`) → segundos."""
    m = _ETIME_RE.match(text.strip())
    if m is None:
        return None
    d, h, mi, s = (int(g or 0) for g in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def format_uptime(seconds: int) -> str:
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = [f"{v}{u}" for v, u in ((d, "d"), (h, "h"), (m, "m")) if v]
    return " ".join(parts) or f"{s}s"


def process_etime(pid: int) -> str:
    r = subprocess.run(  # noqa: S603
        ["/bin/ps", "-o", "etime=", "-p", str(pid)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return r.stdout if r.returncode == 0 else ""


async def probe_socket(timeout: float = PING_TIMEOUT) -> tuple[bool, str | None]:  # noqa: ASYNC109
    """(responde, estado de cuenta) vía hello + ping + el `account.state` inicial."""
    client = Client(paths.socket_path(), kind="cli")
    await client.start()
    try:
        async with asyncio.timeout(timeout):
            await client.wait_connected()
            await client.request(Ping(), timeout=timeout)
            state: str | None = None
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(0.5):
                    async for ev in client.events():
                        if isinstance(ev, AccountState):
                            state = ev.state
                            break
            return True, state
    except (TimeoutError, RequestTimeout, RemoteError, ConnectionError):
        return False, None
    finally:
        await client.stop()


def status(
    run: Runner = launchd.run_launchctl,
    probe: Callable[[], tuple[bool, str | None]] = lambda: asyncio.run(probe_socket()),
    etime: PsRunner = process_etime,
    out: TextIO = sys.stdout,
) -> int:
    """Exit: 0 conectado · 1 no cargado · 2 sin respuesta por el socket · 3 cuenta no conectada."""
    info = launchd.inspect_service(run)
    if not info.loaded:
        out.write("launchd: no cargado\nEjecuta `wsp daemon install`.\n")
        return EXIT_NOT_LOADED
    out.write(f"launchd: cargado ({info.state or 'estado desconocido'})\n")
    out.write(f"pid: {info.pid if info.pid is not None else '-'}\n")
    seconds = parse_etime(etime(info.pid)) if info.pid is not None else None
    out.write(f"uptime: {format_uptime(seconds) if seconds is not None else '-'}\n")
    responds, account = probe()
    if not responds:
        out.write("socket: sin respuesta\n")
        return EXIT_UNREACHABLE
    out.write("socket: responde\n")
    out.write(f"cuenta: {account or 'desconocido'}\n")
    return EXIT_OK if account == "connected" else EXIT_ACCOUNT


def logs(
    n: int = 20,
    follow: bool = True,
    as_json: bool = False,
    out: TextIO = sys.stdout,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] | None = None,
) -> int:
    path = daemon_logs.log_path()
    color = out.isatty() and "NO_COLOR" not in os.environ

    def emit(raw: str) -> None:
        out.write((raw if as_json else daemon_logs.format_line(raw, color)) + "\n")
        out.flush()

    for line in daemon_logs.last_lines(path, n):
        emit(line)
    if not follow:
        return EXIT_OK
    kwargs = {"sleep": sleep} if sleep is not None else {}
    try:
        daemon_logs.follow(path, emit, should_stop=should_stop, **kwargs)
    except KeyboardInterrupt:
        pass
    return EXIT_OK
