"""notify: el texto nunca va en el script AppleScript; osascript siempre mockeado."""

import pytest

from wsp.cli import notify

EVIL = [
    'He said "hi"',
    "it's",
    "back\\slash \\",
    "linea1\nlinea2\r\nlinea3",
    "-e",
    "--",
    '"; do shell script "touch /tmp/pwn"; "',
    'x"\n-e \'do shell script "id"\'',
    "\x1b]0;pwn\x07\x1b[2J\x00\x7f",
    "A" * 100_000,
]


@pytest.mark.parametrize("text", EVIL)
@pytest.mark.parametrize("as_title", [True, False])
def test_build_argv_never_embeds_text(text: str, as_title: bool) -> None:
    argv = notify.build_argv(text if as_title else "t", "b" if as_title else text)
    assert argv[0] == "/usr/bin/osascript"
    sep = argv.index("--")
    assert len(argv) - sep == 3  # `--` + título + cuerpo: solo 2 argumentos tras el separador
    head = argv[:sep]
    assert head[1::2] == ["-e"] * len(notify.SCRIPT)
    assert tuple(head[2::2]) == notify.SCRIPT  # script fijo, intacto
    title, body = argv[-2:]
    assert len(title) <= notify.TITLE_MAX
    assert len(body) <= notify.BODY_MAX
    for part in (title, body):
        assert "\n" not in part and "\r" not in part
        assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in part)
        assert "\x1b" not in part
    for line in notify.SCRIPT:
        assert "do shell script" not in line


def test_build_argv_keeps_normal_text() -> None:
    assert notify.build_argv("Juan", "hola")[-2:] == ["Juan", "hola"]


def test_huge_text_truncated_with_ellipsis() -> None:
    title, body = notify.build_argv("T" * 500, "B" * 5000)[-2:]
    assert len(title) == notify.TITLE_MAX and title.endswith("…")
    assert len(body) == notify.BODY_MAX and body.endswith("…")


# ---- foco ------------------------------------------------------------------------------------


def test_unknown_terminal_never_has_focus() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str | None:
        calls.append(argv)
        return "Terminal"

    assert notify.terminal_has_focus(run, {}) is False
    assert notify.terminal_has_focus(run, {"TERM_PROGRAM": "desconocida"}) is False
    assert calls == []  # ni siquiera consulta osascript


def test_focus_osascript_fails() -> None:
    assert notify.terminal_has_focus(lambda _a: None, {"TERM_PROGRAM": "Apple_Terminal"}) is False


@pytest.mark.parametrize(
    ("term", "front", "expected"),
    [
        ("Apple_Terminal", "Terminal", True),
        ("iTerm.app", "iTerm2", True),
        ("Apple_Terminal", "Safari", False),
        ("ghostty", "ghostty", True),
    ],
)
def test_focus_matches(term: str, front: str, expected: bool) -> None:
    seen: list[list[str]] = []

    def run(argv: list[str]) -> str | None:
        seen.append(argv)
        return front

    assert notify.terminal_has_focus(run, {"TERM_PROGRAM": term}) is expected
    assert seen == [["/usr/bin/osascript", "-e", notify.FRONT_SCRIPT]]


# ---- notify ----------------------------------------------------------------------------------


async def test_notify_skipped_when_terminal_focused() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str | None:
        calls.append(argv)
        return "Terminal"

    sent = await notify.notify("Juan", "hola", run=run, environ={"TERM_PROGRAM": "Apple_Terminal"})
    assert sent is False
    assert len(calls) == 1 and "--" not in calls[0]  # solo la consulta de foco


async def test_notify_sends_when_not_focused() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str | None:
        calls.append(argv)
        return "Safari" if len(calls) == 1 else ""

    sent = await notify.notify("Juan", "hola", run=run, environ={"TERM_PROGRAM": "Apple_Terminal"})
    assert sent is True
    assert calls[-1][-2:] == ["Juan", "hola"]


async def test_notify_unknown_terminal_always_notifies_and_reports_failure() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str | None:
        calls.append(argv)
        return None

    assert await notify.notify("a", "b", run=run, environ={}) is False
    assert len(calls) == 1 and calls[0][-2:] == ["a", "b"]
