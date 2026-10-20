"""Latencia evento→cliente del daemon: inyecta N mensajes falsos y mide cada uno.

Usa `wspd.serve` real (DB, Bridge, Server, socket) con un WaClient falso; el cliente del socket
corre en otro hilo con su propio loop, así un loop del daemon bloqueado se ve como latencia.
t0 = instante en que el evento entra al daemon (`on_event`); t1 = llegada al cliente.

    uv run python tools/latency.py [--events 1000] [--rate 200] [--json] [--label antes]
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import statistics
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

EVENT_TIMEOUT = 30.0


class FakeWa:
    """WaClient mínimo: sin red; `emit` entrega eventos como lo haría neonize."""

    def __init__(self) -> None:
        self.on_event: Callable[[str, Any], Awaitable[None]] | None = None
        self._closed: asyncio.Future[None] | None = None

    def bind(self, on_event: Callable[[str, Any], Awaitable[None]], _on_qr: Any) -> None:
        self.on_event = on_event

    async def connect(self) -> None:
        self._closed = asyncio.get_running_loop().create_future()

    async def wait_closed(self) -> None:
        if self._closed is not None:
            await self._closed

    async def disconnect(self) -> None:
        if self._closed is not None and not self._closed.done():
            self._closed.set_result(None)

    async def send_text(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError

    send_reaction = send_text
    pair_phone = send_text


def _event(i: int) -> SimpleNamespace:
    ns = SimpleNamespace
    chat = ns(User="5215511111111", Server="s.whatsapp.net")
    src = ns(Chat=chat, Sender=chat, IsFromMe=False)
    info = ns(MessageSource=src, ID=f"L{i}", Timestamp=1_700_000_000 + i, Pushname="")
    return ns(Info=info, Message=ns(conversation=f"mensaje {i}"))


def _client_thread(
    n: int, arrivals: dict[str, int], ready: threading.Event, done: threading.Event
) -> None:
    from wsp import paths
    from wsp.protocol.client import Client
    from wsp.protocol.models import MessageNew

    async def run() -> None:
        client = Client(paths.socket_path(), kind="tail")
        await client.start()
        try:
            await client.wait_connected(timeout=10)
            ready.set()
            async with asyncio.timeout(EVENT_TIMEOUT + n / 50):
                async for ev in client.events():
                    if isinstance(ev, MessageNew):
                        arrivals[ev.wa_id] = time.perf_counter_ns()
                        if len(arrivals) >= n:
                            return
        finally:
            await client.stop()
            done.set()

    try:
        asyncio.run(run())
    finally:
        ready.set()
        done.set()


async def measure(n: int, rate: float) -> list[float]:
    """Latencias en ms, una por evento entregado."""
    from wsp.daemon.config import Config
    from wsp.daemon.main import serve

    wa = FakeWa()
    stop = asyncio.Event()
    server = asyncio.create_task(serve(Config(), lambda: wa, stop))  # type: ignore[arg-type,return-value]
    while wa.on_event is None:  # noqa: ASYNC110
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)  # socket listo

    arrivals: dict[str, int] = {}
    ready, done = threading.Event(), threading.Event()
    thread = threading.Thread(
        target=_client_thread, args=(n, arrivals, ready, done), daemon=True, name="latency-client"
    )
    thread.start()
    await asyncio.to_thread(ready.wait, 15)

    sent: dict[str, int] = {}
    interval = 1.0 / rate if rate > 0 else 0.0
    start = time.perf_counter()
    for i in range(n):
        if interval:
            delay = start + i * interval - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
        sent[f"L{i}"] = time.perf_counter_ns()
        await wa.on_event("MessageEv", _event(i))
    await asyncio.to_thread(done.wait, EVENT_TIMEOUT + n / 50)
    thread.join(5)
    stop.set()
    await asyncio.wait_for(server, 10)
    return [(arrivals[k] - sent[k]) / 1e6 for k in sent if k in arrivals]


def percentile(sorted_ms: list[float], p: float) -> float:
    """Percentil por rango más cercano (p en 0–100)."""
    k = max(0, min(len(sorted_ms) - 1, round(p / 100 * len(sorted_ms) + 0.5) - 1))
    return sorted_ms[k]


def summarize(lat: list[float], expected: int) -> dict[str, float]:
    s = sorted(lat)
    return {
        "delivered": len(s),
        "lost": expected - len(s),
        "p50_ms": round(percentile(s, 50), 3),
        "p95_ms": round(percentile(s, 95), 3),
        "p99_ms": round(percentile(s, 99), 3),
        "max_ms": round(s[-1], 3),
        "mean_ms": round(statistics.fmean(s), 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--events", type=int, default=1000)
    ap.add_argument("--rate", type=float, default=200.0, help="eventos/s; 0 = ráfaga sin pausa")
    ap.add_argument("--label", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    home = tempfile.mkdtemp(prefix="wsp-lat-")
    os.environ["WSP_HOME"] = os.path.join(home, "h")
    logging.disable(logging.CRITICAL)
    try:
        from wsp import paths

        paths.ensure_dirs()
        lat = asyncio.run(measure(args.events, args.rate))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    if not lat:
        print("sin eventos entregados")
        return 1
    result = summarize(lat, args.events)
    if args.json:
        print(json.dumps({"label": args.label, "rate": args.rate, **result}))
    else:
        tag = f"[{args.label}] " if args.label else ""
        print(
            f"{tag}n={args.events} rate={args.rate:g}/s entregados={result['delivered']:.0f} "
            f"perdidos={result['lost']:.0f} p50={result['p50_ms']}ms p95={result['p95_ms']}ms "
            f"p99={result['p99_ms']}ms max={result['max_ms']}ms"
        )
    return 0 if result["lost"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
