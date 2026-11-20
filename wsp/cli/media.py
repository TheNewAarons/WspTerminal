"""Stickers en línea (protocolo gráfico de Kitty: Ghostty, Kitty, WezTerm) y audios con `afplay`.

El daemon ya guarda los stickers como PNG; `afplay` (macOS) reproduce ogg/opus, m4a y mp3.
"""

import asyncio
import base64
import os
from pathlib import Path
from typing import Final

_CHUNK: Final = 4096
_KITTY_TERMS: Final = frozenset({"ghostty", "kitty", "WezTerm"})


def kitty_supported() -> bool:
    return os.environ.get("TERM_PROGRAM") in _KITTY_TERMS or "kitty" in os.environ.get("TERM", "")


def kitty_image(png: bytes) -> str:
    """PNG → secuencia APC de Kitty (transmitir y mostrar), en trozos de 4096 bytes base64."""
    data = base64.standard_b64encode(png).decode()
    chunks = [data[i : i + _CHUNK] for i in range(0, len(data), _CHUNK)] or [""]
    out = []
    for i, chunk in enumerate(chunks):
        more = int(i < len(chunks) - 1)
        head = f"a=T,f=100,m={more}" if i == 0 else f"m={more}"
        out.append(f"\x1b_G{head};{chunk}\x1b\\")
    return "".join(out)


async def play(path: Path) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        "afplay", str(path), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
