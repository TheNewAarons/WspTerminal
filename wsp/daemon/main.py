"""Entrypoint de `wspd`."""

from wsp import __version__


def run() -> None:
    """Punto de entrada del daemon."""
    print(f"wspd {__version__}")
