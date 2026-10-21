"""NeonizeClient.pair_phone reintenta mientras el cliente nativo aún es nil."""

from typing import Any

import pytest

from wsp.daemon import session
from wsp.daemon.session import NeonizeClient


class _Native:
    def __init__(
        self, failures: int, error: str = "client is nil: ensure client is connected"
    ) -> None:
        self.calls = 0
        self._failures = failures
        self._error = error

    async def PairPhone(self, phone: str, push: bool) -> str:  # noqa: N802
        self.calls += 1
        if self.calls <= self._failures:
            raise RuntimeError(self._error)
        return "ABCD-1234"


def _client(native: Any) -> NeonizeClient:
    c = object.__new__(NeonizeClient)
    c._c = native
    return c


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session, "PAIR_PHONE_DELAY", 0)


async def test_retries_until_client_ready() -> None:
    native = _Native(failures=3)
    assert await _client(native).pair_phone("56912345678") == "ABCD-1234"
    assert native.calls == 4


async def test_gives_up_after_max_retries() -> None:
    native = _Native(failures=10**6)
    with pytest.raises(RuntimeError, match="client is nil"):
        await _client(native).pair_phone("56912345678")
    assert native.calls == session.PAIR_PHONE_RETRIES


async def test_other_errors_not_retried() -> None:
    native = _Native(failures=10**6, error="otro error")
    with pytest.raises(RuntimeError, match="otro error"):
        await _client(native).pair_phone("56912345678")
    assert native.calls == 1
