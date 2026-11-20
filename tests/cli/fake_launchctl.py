"""`launchctl` simulado: registra llamadas y modela si el servicio está cargado."""

import subprocess

from wsp.cli.launchd import service_target


class FakeLaunchctl:
    def __init__(
        self,
        loaded: bool = False,
        pid: int | None = None,
        state: str = "running",
        fail: dict[str, int] | None = None,
    ) -> None:
        self.loaded = loaded
        self.pid = pid
        self.state = state
        self.fail = fail or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> "subprocess.CompletedProcess[str]":
        self.calls.append(args)
        verb = args[0]
        if (rc := self.fail.get(verb)) is not None:
            return subprocess.CompletedProcess(args, rc, "", f"{verb} error simulado")
        if verb == "print":
            if not self.loaded:
                return subprocess.CompletedProcess(args, 113, "", "not found")
            lines = [service_target() + " = {", f"\tstate = {self.state}"]
            if self.pid is not None:
                lines.append(f"\tpid = {self.pid}")
            return subprocess.CompletedProcess(args, 0, "\n".join(lines) + "\n}\n", "")
        if verb == "bootstrap":
            self.loaded = True
        elif verb == "bootout":
            self.loaded = False
        return subprocess.CompletedProcess(args, 0, "", "")

    @property
    def verbs(self) -> list[str]:
        return [c[0] for c in self.calls]
