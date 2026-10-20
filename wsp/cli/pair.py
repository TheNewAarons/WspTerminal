"""`wsp pair`: vincula el dispositivo (QR en terminal o código con `--phone`) y sale al conectar."""

import asyncio
import contextlib
import re
import sys
from collections.abc import Callable
from typing import Any, Final, TextIO

import qrcode

from wsp.daemon.session import (
    EXIT_ALREADY_RUNNING,
    AlreadyRunningError,
    InstanceLock,
    NeonizeClient,
    WaClient,
    secure_session_files,
)

PAIR_TIMEOUT: Final = 180.0
_PHONE_RE: Final = re.compile(r"\+?\d{8,15}")
_CONNECTED: Final = frozenset({"ConnectedEv", "PairStatusEv"})


def render_qr(data: bytes, out: TextIO) -> None:
    """Dibuja el QR como ASCII en `out`."""
    qr = qrcode.QRCode(border=1)
    qr.add_data(data)
    qr.make()
    qr.print_ascii(out=out, invert=True)


async def pair(
    client: WaClient,
    out: TextIO,
    phone: str | None = None,
    wait_seconds: float = PAIR_TIMEOUT,
) -> bool:
    """Conecta y espera el vínculo. True si quedó conectado dentro del tiempo."""
    connected = asyncio.Event()

    async def on_event(kind: str, _ev: Any) -> None:
        if kind in _CONNECTED:
            connected.set()

    async def on_qr(data: bytes) -> None:
        if phone is None:
            out.write("Escanea el QR en WhatsApp > Dispositivos vinculados:\n")
            render_qr(data, out)
            out.flush()

    client.bind(on_event, on_qr)
    await client.connect()
    try:
        if phone is not None:
            code = await client.pair_phone(phone)
            out.write(f"Código de vinculación: {code}\n")
            out.flush()
        async with asyncio.timeout(wait_seconds):
            await connected.wait()
    except TimeoutError:
        return False
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()
    return True


def run_pair(
    phone: str | None,
    client_factory: Callable[[], WaClient] = NeonizeClient,
    out: TextIO | None = None,
) -> int:
    """Código de salida: 0 ok, 1 error/timeout, 2 si hay otro wspd o `wsp pair` activo."""
    out = out or sys.stdout
    if phone is not None and not _PHONE_RE.fullmatch(phone):
        sys.stderr.write("wsp: --phone debe ser un número con código de país (solo dígitos)\n")
        return 1
    lock = InstanceLock()
    try:
        lock.acquire()
    except AlreadyRunningError as e:
        sys.stderr.write(f"wsp: {e}; detén wspd antes de vincular\n")
        return EXIT_ALREADY_RUNNING
    try:
        ok = asyncio.run(pair(client_factory(), out, phone.lstrip("+") if phone else None))
        secure_session_files()
    finally:
        lock.release()
    if not ok:
        sys.stderr.write("wsp: tiempo agotado sin vincular\n")
        return 1
    out.write("Vinculado.\n")
    return 0
