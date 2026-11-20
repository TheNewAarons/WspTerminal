"""Descarga de stickers y audios del daemon."""

import asyncio
import base64
import stat
from pathlib import Path
from typing import Any

import pytest

from tests.daemon.fakes import message_ev, ns, text_msg
from wsp import paths
from wsp.cli import media as cli_media
from wsp.daemon import media


async def _convert(src: Path, dst: Path) -> bool:
    dst.write_bytes(b"PNG:" + src.read_bytes())  # noqa: ASYNC240
    return True


async def _fail_convert(src: Path, dst: Path) -> bool:
    return False


def _download(data: bytes = b"DATA") -> Any:
    async def dl(_msg: Any) -> bytes:
        return data

    return dl


async def test_saves_sticker_as_png_with_600(wsp_home: Path) -> None:
    ev = message_ev(ns(stickerMessage=ns(fileLength=4)), wa_id="AB12")
    await media.save(_download(), ev, _convert)
    path = paths.media_file("AB12", "png")
    assert path.read_bytes() == b"PNG:DATA"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert paths.find_media("AB12") == path
    assert [p.name for p in paths.media_dir().iterdir()] == ["AB12.png"]  # sin temporales


async def test_sticker_conversion_failure_leaves_nothing(wsp_home: Path) -> None:
    await media.save(_download(), message_ev(ns(stickerMessage=ns()), wa_id="B2"), _fail_convert)
    assert paths.find_media("B2") is None
    assert not any(paths.media_dir().iterdir())


async def test_failed_conversion_removes_partial_output(wsp_home: Path) -> None:
    async def half(src: Path, dst: Path) -> bool:
        dst.write_bytes(b"medio")  # noqa: ASYNC240
        return False

    await media.save(_download(), message_ev(ns(stickerMessage=ns()), wa_id="B3"), half)
    assert not any(paths.media_dir().iterdir())


async def test_concurrent_redelivery_downloads_once(wsp_home: Path) -> None:
    calls = 0
    gate = asyncio.Event()

    async def slow(_msg: Any) -> bytes:
        nonlocal calls
        calls += 1
        await gate.wait()
        return b"DATA"

    ev = message_ev(ns(audioMessage=ns()), wa_id="R1")
    first = asyncio.create_task(media.save(slow, ev))
    await asyncio.sleep(0)
    await media.save(slow, ev)  # reentrega mientras la primera sigue en curso
    gate.set()
    await first
    assert calls == 1
    assert paths.media_file("R1", "ogg").read_bytes() == b"DATA"


async def test_audio_extension_from_mimetype(wsp_home: Path) -> None:
    msg = ns(audioMessage=ns(mimetype="audio/mp4", fileLength=4))
    await media.save(_download(), message_ev(msg, wa_id="C3"))
    assert paths.find_media("C3") == paths.media_file("C3", "m4a")
    assert paths.media_file("C3", "m4a").read_bytes() == b"DATA"


async def test_unwraps_ephemeral(wsp_home: Path) -> None:
    msg = ns(ephemeralMessage=ns(message=ns(audioMessage=ns(mimetype="audio/ogg; codecs=opus"))))
    await media.save(_download(), message_ev(msg, wa_id="D4"))
    assert paths.find_media("D4") == paths.media_file("D4", "ogg")


@pytest.mark.parametrize(
    ("msg", "wa_id"),
    [
        (text_msg("hola"), "E5"),  # no es medio
        (ns(stickerMessage=ns(fileLength=media.MAX_BYTES + 1)), "E6"),  # demasiado grande
        (ns(stickerMessage=ns()), "../x"),  # id inseguro
    ],
)
async def test_skips(wsp_home: Path, msg: Any, wa_id: str) -> None:
    await media.save(_download(), message_ev(msg, wa_id=wa_id), _convert)
    assert not paths.media_dir().exists() or not any(paths.media_dir().iterdir())


async def test_download_error_is_swallowed(wsp_home: Path) -> None:
    async def boom(_msg: Any) -> bytes:
        raise RuntimeError("red")

    await media.save(boom, message_ev(ns(stickerMessage=ns()), wa_id="F7"))
    assert paths.find_media("F7") is None


@pytest.mark.parametrize("wa_id", ["", "a/b", "..", "á", "x" * 129])
def test_media_file_rejects_unsafe(wa_id: str) -> None:
    with pytest.raises(ValueError):
        paths.media_file(wa_id, "ogg")


def test_kitty_image_chunks() -> None:
    png = bytes(range(256)) * 20  # > 4096 en base64
    seq = cli_media.kitty_image(png)
    parts = seq.split("\x1b\\")[:-1]
    assert len(parts) > 1
    assert parts[0].startswith("\x1b_Ga=T,f=100,m=1;")
    assert parts[-1].startswith("\x1b_Gm=0;")
    payload = "".join(p.split(";", 1)[1] for p in parts)
    assert base64.standard_b64decode(payload) == png
