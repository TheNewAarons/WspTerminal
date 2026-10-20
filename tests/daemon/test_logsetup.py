"""Redacción de teléfonos y contenido; archivo de log JSON en 600."""

import json
import logging
import stat
from pathlib import Path

import pytest
import structlog

from wsp.daemon.logsetup import configure_logging, mask_phones, redact

pytestmark = pytest.mark.usefixtures("restore_process_state")


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("+56 9 8765 4321", "+56 9 ****4321"),
        ("5215512345678", "****5678"),
        ("5215512345678@s.whatsapp.net", "****5678@s.whatsapp.net"),
        ("llamar a 600-123-4567 ya", "llamar a ****4567 ya"),
    ],
)
def test_mask_phones(raw: str, masked: str) -> None:
    assert mask_phones(raw) == masked


def test_mask_phones_with_dashes_hides_middle_digits() -> None:
    out = mask_phones("+34 600-123-456")
    assert out.startswith("+34 ") and out.endswith("3456")
    assert "600" not in out and "123" not in out


def test_short_numbers_untouched() -> None:
    assert mask_phones("n=12345 attempt=3") == "n=12345 attempt=3"


def test_redact_omits_content_keys_and_masks_numbers() -> None:
    ev = {
        "event": "algo",
        "body": "hola secreto",
        "Text": "otro",
        "caption": "c",
        "message": "m",
        "content": "c",
        "payload": {"a": 1},
        "title": "Grupo",
        "push_name": "Juan",
        "jid": "5215512345678@s.whatsapp.net",
        "nested": {"body": "x", "phone": "+56 9 8765 4321", "list": ["5215512345678", 3]},
        "n": 7,
    }
    out = redact(None, "info", ev)
    for k in ("body", "Text", "caption", "message", "content", "payload", "title", "push_name"):
        assert out[k] == "<omitido>"
    assert out["jid"] == "****5678@s.whatsapp.net"
    assert out["nested"]["body"] == "<omitido>"
    assert out["nested"]["phone"] == "+56 9 ****4321"
    assert out["nested"]["list"] == ["****5678", 3]
    assert out["n"] == 7 and out["event"] == "algo"


def test_redact_keeps_non_string_event() -> None:
    assert redact(None, "info", {"event": 5})["event"] == 5


def test_configure_logging_writes_private_json_without_content(short_path: Path) -> None:
    log_file = short_path / "wspd.log"
    handler = configure_logging(log_file, "DEBUG")
    structlog.get_logger("t").info("evento", body="SECRETO-STRUCT", jid="5215512345678@x")
    logging.getLogger("wsp.test").warning("hola +56 9 8765 4321 mensaje")
    logging.getLogger("wsp.test").debug("detalle")
    handler.flush()
    raw = log_file.read_text()
    assert "SECRETO-STRUCT" not in raw
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600
    lines = [json.loads(line) for line in raw.splitlines()]
    assert len(lines) == 3
    assert all("timestamp" in line and "level" in line for line in lines)
    assert lines[0]["body"] == "<omitido>" and lines[0]["jid"] == "****5678@x"
    assert "+56 9 ****4321" in lines[1]["event"]
    assert "8765" not in raw


def test_configure_logging_replaces_old_handlers_and_sets_level(short_path: Path) -> None:
    h1 = configure_logging(short_path / "a.log", "INFO")
    h2 = configure_logging(short_path / "b.log", "WARNING")
    root = logging.getLogger()
    assert h1 not in root.handlers and h2 in root.handlers
    assert root.level == logging.WARNING
    logging.getLogger("x").info("no debe salir")
    h2.flush()
    assert (short_path / "b.log").read_text() == ""


def test_log_file_mode_forced_even_if_preexisting_wide(short_path: Path) -> None:
    log_file = short_path / "w.log"
    log_file.write_text("")
    log_file.chmod(0o644)
    handler = configure_logging(log_file)
    logging.getLogger("x").info("hola")
    handler.flush()
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600
