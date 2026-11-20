"""Entrypoint de `wsp`."""

import argparse
import asyncio
import os
import signal
import sys
from collections.abc import Coroutine, Sequence
from typing import Any

from wsp import __version__
from wsp.cli import config_cmd, daemon_cmd, render, repl, script
from wsp.cli.pair import run_pair
from wsp.daemon.config import ConfigError, load_config


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wsp", description="Cliente de WhatsApp para la terminal.")
    p.add_argument("--version", action="version", version=f"wsp {__version__}")
    p.add_argument("--no-color", action="store_true", help="sin colores ANSI (también NO_COLOR)")
    sub = p.add_subparsers(dest="cmd")
    pair = sub.add_parser("pair", help="vincular el dispositivo (QR o código)")
    pair.add_argument("--phone", metavar="NÚMERO", help="vincular con código en vez de QR")
    daemon = sub.add_parser("daemon", help="gestionar el LaunchAgent de wspd")
    dsub = daemon.add_subparsers(dest="daemon_cmd", required=True)
    dsub.add_parser("install", help="instalar y cargar el LaunchAgent (idempotente)")
    dsub.add_parser("uninstall", help="descargar y borrar el LaunchAgent (conserva datos)")
    dsub.add_parser("status", help="estado de launchd, socket y cuenta")
    dsub.add_parser("restart", help="reiniciar el daemon (kickstart -k)")
    logs = dsub.add_parser("logs", help="ver el log del daemon (sigue por defecto)")
    logs.add_argument("-n", type=int, default=20, metavar="N", help="líneas iniciales (20)")
    logs.add_argument("--no-follow", action="store_true", help="no seguir; imprimir y salir")
    logs.add_argument("--json", action="store_true", help="líneas JSON sin formatear")
    send = sub.add_parser("send", help="enviar un mensaje y salir (0 = enviado)")
    send.add_argument("chat", help="nombre aproximado o jid del chat")
    send.add_argument("text", help="texto del mensaje; `-` lee de stdin")
    send.add_argument("--wait-delivered", action="store_true", help="esperar a ✓✓ (30 s)")
    tail = sub.add_parser("tail", help="imprimir mensajes en vivo (opcional: un chat)")
    tail.add_argument("chat", nargs="?", help="limitar a este chat")
    tail.add_argument("--json", action="store_true", help="un evento JSON por línea (para jq)")
    sub.add_parser("config", help="mostrar la configuración efectiva y su ruta")
    return p


def _daemon(args: argparse.Namespace) -> int:
    match args.daemon_cmd:
        case "install":
            return daemon_cmd.install()
        case "uninstall":
            return daemon_cmd.uninstall()
        case "status":
            return daemon_cmd.status()
        case "restart":
            return daemon_cmd.restart()
        case _:
            return daemon_cmd.logs(args.n, not args.no_follow, args.json)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "pair":
        return run_pair(args.phone)
    if args.cmd == "daemon":
        return _daemon(args)
    if args.cmd == "config":
        return config_cmd.show()
    pal = _palette(args.no_color)
    if args.cmd == "send":
        return _run(script.send(args.chat, _read(args.text), wait_delivered=args.wait_delivered))
    if args.cmd == "tail":
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # `wsp tail | head` termina sin traceback
        return _run(script.tail(args.chat, as_json=args.json, pal=pal))
    return _repl(pal)


def _palette(no_color: bool) -> render.Palette:
    return render.Palette(render.color_enabled(no_color, dict(os.environ), sys.stdout.isatty()))


def _read(text: str) -> str:
    return script.read_text(text, sys.stdin)


def _run(coro: Coroutine[Any, Any, int]) -> int:
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        return 130


def _repl(pal: render.Palette) -> int:
    try:
        cfg = load_config()
    except ConfigError as e:
        sys.stderr.write(f"wsp: {e}\n")
        return 1
    return _run(repl.run(pal=pal, notifications=cfg.notifications))


def app() -> None:
    """Punto de entrada del CLI."""
    sys.exit(main())
