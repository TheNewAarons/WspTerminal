"""Utilidades de tiempo compartidas por la traducción de eventos y el adaptador de sesión."""

from typing import Any


def norm_ts(ts: Any) -> int:
    """Segundos Unix; neonize puede entregar milisegundos (o None/0 → 0)."""
    value = int(ts or 0)
    return value // 1000 if value > 10**11 else value
