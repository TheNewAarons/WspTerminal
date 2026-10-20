"""Entrypoint de `wsp`."""

import argparse
import sys
from collections.abc import Sequence

from wsp import __version__
from wsp.cli import daemon_cmd
from wsp.cli.pair import run_pair


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wsp", description="Cliente de WhatsApp para la terminal.")
    p.add_argument("--version", action="version", version=f"wsp {__version__}")
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
    sys.stdout.write(f"wsp {__version__}\n")
    return 0


def app() -> None:
    """Punto de entrada del CLI."""
    sys.exit(main())
