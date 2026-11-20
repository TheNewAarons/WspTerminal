"""Nombres de contactos y grupos: se piden a WhatsApp y se guardan en `chats.title`/`contacts`."""

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from wsp.store import repo
from wsp.store.writer import Writer

if TYPE_CHECKING:
    from wsp.daemon.session import WaClient

log = logging.getLogger(__name__)

DEBOUNCE_SECONDS: Final = 3.0
_MAX_NAME: Final = 256
_DIRECT_SUFFIX: Final = "@s.whatsapp.net"


@dataclass(frozen=True, slots=True)
class NameEntry:
    jid: str
    name: str


def apply_names(conn: sqlite3.Connection, entries: list[NameEntry]) -> int:
    """Títulos de chats existentes y push_name de contactos directos; devuelve cuántos."""
    n = 0
    for e in entries:
        name = e.name.strip()[:_MAX_NAME]
        if not name:
            continue
        repo.set_chat_title(conn, e.jid, name)
        if e.jid.endswith(_DIRECT_SUFFIX):
            repo.upsert_contact(conn, e.jid, push_name=name)
        n += 1
    return n


class NameSync:
    """Pide nombres al cliente tras conectar o recibir historial; agrupa ráfagas (debounce)."""

    def __init__(
        self,
        client: "WaClient",
        writer: Writer,
        *,
        delay: float = DEBOUNCE_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._writer = writer
        self._delay = delay
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None

    def request(self) -> None:
        """Agenda una sincronización; una nueva petición reemplaza la pendiente."""
        if self._task is not None:
            self._task.cancel()
        self._task = asyncio.create_task(self._later(), name="name-sync")

    async def _later(self) -> None:
        await self._sleep(self._delay)
        await self.run_once()

    async def run_once(self) -> int:
        try:
            entries = await self._client.fetch_names()
            n = await self._writer.run(apply_names, entries)
        except Exception as e:  # sin contenido: solo el tipo
            log.warning("sync de nombres falló exc=%s", type(e).__name__)
            return 0
        log.info("nombres sincronizados n=%d", n)
        return n

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
