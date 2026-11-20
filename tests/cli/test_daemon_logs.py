import io
import json
from pathlib import Path

import pytest

from wsp import paths
from wsp.cli import daemon_cmd, daemon_logs


def _line(event: str, level: str = "info", **extra: object) -> str:
    return json.dumps(
        {
            "event": event,
            "level": level,
            "logger": "wsp.daemon.main",
            "timestamp": "2026-01-01T14:02:11Z",
            **extra,
        }
    )


def test_format_line_readable() -> None:
    text = daemon_logs.format_line(_line("listo", code="x"))
    assert "INFO" in text
    assert "wsp.daemon.main" in text
    assert "listo" in text
    assert "code=x" in text
    assert "{" not in text


def test_format_line_non_json_and_control_chars() -> None:
    assert daemon_logs.format_line("panic: boom\x1b[31m") == "panic: boom[31m"
    assert daemon_logs.format_line("[1, 2]") == "[1, 2]"


def test_format_line_bad_timestamp() -> None:
    raw = json.dumps({"event": "e", "level": "warning", "timestamp": "no"})
    assert daemon_logs.format_line(raw).startswith("--:--:-- WARNING")


def test_format_line_color() -> None:
    assert "\x1b[31m" in daemon_logs.format_line(_line("e", "error"), color=True)
    assert "\x1b" not in daemon_logs.format_line(_line("e", "error"), color=False)


def test_last_lines_spans_rotated_files(wsp_home: Path) -> None:
    base = daemon_logs.log_path()
    base.with_name(base.name + ".1").write_text("a\nb\n")
    base.write_text("c\nd\n")
    assert daemon_logs.last_lines(base, 3) == ["b", "c", "d"]
    assert daemon_logs.last_lines(base, 1) == ["d"]
    assert daemon_logs.last_lines(base, 99) == ["a", "b", "c", "d"]


def test_last_lines_missing(wsp_home: Path) -> None:
    assert daemon_logs.last_lines(daemon_logs.log_path(), 5) == []


def test_logs_no_follow_formats(wsp_home: Path) -> None:
    daemon_logs.log_path().write_text(_line("uno") + "\n" + _line("dos") + "\n")
    out = io.StringIO()
    assert daemon_cmd.logs(n=1, follow=False, out=out) == 0
    assert "dos" in out.getvalue()
    assert "uno" not in out.getvalue()


def test_logs_json_raw(wsp_home: Path) -> None:
    raw = _line("uno")
    daemon_logs.log_path().write_text(raw + "\n")
    out = io.StringIO()
    daemon_cmd.logs(n=5, follow=False, as_json=True, out=out)
    assert out.getvalue() == raw + "\n"


def test_follow_new_lines_rotation_and_truncation(wsp_home: Path) -> None:
    path = daemon_logs.log_path()
    path.write_text("viejo\n")
    got: list[str] = []
    ticks = 0

    def sleep(_s: float) -> None:
        nonlocal ticks
        if ticks == 0:
            with path.open("a") as f:
                f.write("nuevo1\nparc")
        elif ticks == 1:
            with path.open("a") as f:
                f.write("ial\n")
        elif ticks == 2:  # rotación: inodo nuevo
            path.rename(path.with_name(path.name + ".1"))
            path.write_text("tras-rotar\n")
        elif ticks == 3:  # truncado en el lugar
            path.write_text("t\n")
        ticks += 1

    daemon_logs.follow(path, got.append, should_stop=lambda: ticks >= 5, sleep=sleep)
    assert got == ["nuevo1", "parcial", "tras-rotar", "t"]


def test_follow_waits_for_missing_file(wsp_home: Path) -> None:
    path = daemon_logs.log_path()
    got: list[str] = []
    ticks = 0

    def sleep(_s: float) -> None:
        nonlocal ticks
        if ticks == 1:
            path.write_text("aparece\n")
        ticks += 1

    daemon_logs.follow(path, got.append, should_stop=lambda: ticks >= 3, sleep=sleep)
    assert got == ["aparece"]


def test_logs_follow_prints_initial_then_new(wsp_home: Path) -> None:
    path = paths.state_dir() / "wspd.log"
    path.write_text(_line("inicial") + "\n")
    out = io.StringIO()
    ticks = 0

    def sleep(_s: float) -> None:
        nonlocal ticks
        if ticks == 0:
            with path.open("a") as f:
                f.write(_line("reciente") + "\n")
        ticks += 1

    code = daemon_cmd.logs(n=5, out=out, should_stop=lambda: ticks >= 2, sleep=sleep)
    assert code == 0
    text = out.getvalue()
    assert text.index("inicial") < text.index("reciente")


def test_logs_ctrl_c_exits_cleanly(wsp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(daemon_logs, "follow", boom)
    assert daemon_cmd.logs(out=io.StringIO()) == 0
