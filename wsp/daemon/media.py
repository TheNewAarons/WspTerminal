"""Descarga de stickers y audios recibidos a `data_dir()/media/<wa_id>.<ext>` (600).

Solo mensajes en vivo (`MessageEv`); el historial no se descarga. El nombre de archivo sale del
`wa_id` validado en `paths.media_file`; los errores se registran por tipo, nunca con contenido.
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final

from wsp import paths
from wsp.daemon.events import _unwrap, has

log = logging.getLogger(__name__)

MAX_BYTES: Final = 16 * 1024 * 1024
_AUDIO_EXT: Final = {
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/aac": "aac",
}

STICKER_PX: Final = 160
CONCURRENCY: Final = 3  # descargas simultáneas
MAX_PENDING: Final = 50  # en cola; por encima se descartan (ráfaga de un remitente)
DOWNLOAD_TIMEOUT: Final = 60.0
CONVERT_TIMEOUT: Final = 10.0

_in_flight: set[str] = set()  # wa_id en descarga: evita dos escritores ante reentregas

Download = Callable[[Any], Awaitable[bytes]]
Convert = Callable[[Path, Path], Awaitable[bool]]


def target(ev: Any) -> tuple[str, Any, str] | None:
    """`(wa_id, mensaje desenvuelto, extensión)` si el evento trae sticker o audio; si no, None."""
    msg = _unwrap(ev.Message)
    if has(msg, "stickerMessage"):
        media, ext = msg.stickerMessage, "png"  # se guarda convertido para pintarlo en terminal
    elif has(msg, "audioMessage"):
        media = msg.audioMessage
        mime = str(getattr(media, "mimetype", "") or "").split(";")[0].strip().lower()
        ext = _AUDIO_EXT.get(mime, "ogg")
    else:
        return None
    if int(getattr(media, "fileLength", 0) or 0) > MAX_BYTES:
        return None
    return str(ev.Info.ID or ""), msg, ext


async def sticker_png(src: Path, dst: Path) -> bool:
    """webp → png reducido a `STICKER_PX` con `sips` (macOS); False si falla."""
    proc = await asyncio.create_subprocess_exec(
        "sips", "-s", "format", "png", "-Z", str(STICKER_PX), str(src), "--out", str(dst),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    try:
        return await asyncio.wait_for(proc.wait(), CONVERT_TIMEOUT) == 0
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False


async def save(download: Download, ev: Any, convert: Convert = sticker_png) -> None:
    """Descarga y guarda el medio del evento (no-op si no aplica, ya existe o está en curso)."""
    try:
        t = target(ev)
        if t is None:
            return
        wa_id, msg, ext = t
        path = paths.media_file(wa_id, ext)
        if path.exists() or wa_id in _in_flight:
            return
        _in_flight.add(wa_id)
        raw, conv = path.with_suffix(".part"), path.with_suffix(".png.part")
        try:
            data = await asyncio.wait_for(download(msg), DOWNLOAD_TIMEOUT)
            if len(data) > MAX_BYTES:
                return
            await asyncio.to_thread(_write, raw, data)
            if ext != "png":
                raw.replace(path)
            elif await convert(raw, conv):
                conv.chmod(0o600)
                conv.replace(path)
            else:
                log.warning("media: conversión de sticker falló")
        finally:
            _in_flight.discard(wa_id)
            raw.unlink(missing_ok=True)
            conv.unlink(missing_ok=True)
    except Exception as e:  # una descarga fallida no debe afectar al daemon
        log.warning("media: descarga falló exc=%s", type(e).__name__)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
