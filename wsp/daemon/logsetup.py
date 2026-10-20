"""Logging JSON (structlog) con rotación y redacción de números y contenido.

Nunca se escribe el cuerpo de un mensaje; todo número de teléfono se enmascara
(`+56 9 8765 4321` → `+56 9 ****4321`), también dentro de JIDs.
"""

import logging
import logging.handlers
import os
import re
from pathlib import Path
from typing import Any, Final

import structlog

LOG_MAX_BYTES: Final = 5 * 1024 * 1024
LOG_BACKUPS: Final = 3
_FORBIDDEN_KEYS: Final = frozenset(
    {"body", "text", "caption", "message", "content", "payload", "title", "push_name"}
)
# 7+ dígitos con separadores simples (espacio . - paréntesis) entre ellos.
_PHONE_RE: Final = re.compile(r"\+?\d(?:[\s().-]{0,2}\d){6,}")
_SEP_RE: Final = re.compile(r"[\s().-]+")
_KEEP_PREFIX_DIGITS: Final = 3


def _mask_match(m: re.Match[str]) -> str:
    s = m.group(0)
    tail = re.sub(r"\D", "", s)[-4:]
    if not s.startswith("+"):
        return f"****{tail}"
    prefix: list[str] = []
    total = 0
    for group in _SEP_RE.split(s.lstrip("+"))[:-1]:  # el último grupo nunca se conserva
        if total + len(group) > _KEEP_PREFIX_DIGITS:
            break
        prefix.append(group)
        total += len(group)
    return "+" + "".join(f"{g} " for g in prefix) + f"****{tail}"


def mask_phones(text: str) -> str:
    """Enmascara números de teléfono dejando prefijo corto y últimos 4 dígitos."""
    return _PHONE_RE.sub(_mask_match, text)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_phones(value)
    if isinstance(value, dict):
        return {k: _redact_item(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_value(v) for v in value]
    return value


def _redact_item(key: str, value: Any) -> Any:
    if key.lower() in _FORBIDDEN_KEYS:
        return "<omitido>"
    return value if key == "timestamp" else _redact_value(value)


def redact(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Procesador structlog: omite claves de contenido y enmascara números."""
    out: dict[str, Any] = {}
    for k, v in event_dict.items():
        out[k] = v if k == "event" and not isinstance(v, str) else _redact_item(k, v)
    return out


class _PrivateRotatingHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler con archivos nuevos en 600."""

    def _open(self) -> Any:
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding)


def configure_logging(log_file: Path, level: str = "INFO") -> logging.Handler:
    """Enruta structlog y `logging` a un JSON rotativo (5 MB × 3). Devuelve el handler."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        redact,
    ]
    handler = _PrivateRotatingHandler(
        log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[structlog.stdlib.add_logger_name, *shared],
            processors=[
                structlog.processors.format_exc_info,
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level)
    structlog.configure(
        processors=[
            structlog.stdlib.add_logger_name,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    return handler
