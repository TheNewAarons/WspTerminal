"""Entrypoint de `wsp`."""

from wsp import __version__


def app() -> None:
    """Punto de entrada del CLI."""
    print(f"wsp {__version__}")
