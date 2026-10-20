"""Entrypoint de `wsp`."""

import argparse
import sys
from collections.abc import Sequence

from wsp import __version__
from wsp.cli.pair import run_pair


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wsp", description="Cliente de WhatsApp para la terminal.")
    p.add_argument("--version", action="version", version=f"wsp {__version__}")
    sub = p.add_subparsers(dest="cmd")
    pair = sub.add_parser("pair", help="vincular el dispositivo (QR o código)")
    pair.add_argument("--phone", metavar="NÚMERO", help="vincular con código en vez de QR")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "pair":
        return run_pair(args.phone)
    sys.stdout.write(f"wsp {__version__}\n")
    return 0


def app() -> None:
    """Punto de entrada del CLI."""
    sys.exit(main())
