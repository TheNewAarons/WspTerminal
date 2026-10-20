"""wsp pair: QR, código por teléfono, éxito, timeout, lock y validación."""

import io
from pathlib import Path

import pytest

from tests.daemon.fakes import FakeClient
from wsp.cli import pair as pair_mod
from wsp.cli.pair import pair, render_qr, run_pair
from wsp.daemon.session import InstanceLock, WaClient


def client_that(*events: str, qr: bytes | None = None) -> FakeClient:
    c = FakeClient()

    async def after(cl: FakeClient) -> None:
        if qr is not None:
            assert cl.on_qr is not None
            await cl.on_qr(qr)
        for kind in events:
            await cl.emit(kind)

    c.after_connect = after
    return c


def test_render_qr_draws_ascii() -> None:
    out = io.StringIO()
    render_qr(b"https://example.test/abc", out)
    assert len(out.getvalue().splitlines()) > 10


async def test_qr_is_printed_then_connected() -> None:
    out = io.StringIO()
    c = client_that("ConnectedEv", qr=b"2@qr-data")
    assert await pair(c, out, wait_seconds=2) is True
    text = out.getvalue()
    assert "Escanea el QR" in text
    assert len(text.splitlines()) > 10
    assert c.disconnects == 1


async def test_pair_status_event_also_counts() -> None:
    assert await pair(client_that("PairStatusEv"), io.StringIO(), wait_seconds=2) is True


async def test_phone_prints_code_and_no_qr() -> None:
    out = io.StringIO()
    c = client_that("ConnectedEv", qr=b"2@qr-data")
    assert await pair(c, out, phone="5215512345678", wait_seconds=2) is True
    assert "Código de vinculación: ABCD-1234" in out.getvalue()
    assert "Escanea" not in out.getvalue()
    assert c.phones == ["5215512345678"]


async def test_timeout_returns_false_and_disconnects() -> None:
    c = FakeClient()
    assert await pair(c, io.StringIO(), wait_seconds=0.05) is False
    assert c.disconnects == 1


async def test_other_events_do_not_connect() -> None:
    c = client_that("DisconnectedEv", "MessageEv")
    assert await pair(c, io.StringIO(), wait_seconds=0.05) is False


async def test_disconnect_error_is_swallowed() -> None:
    c = client_that("ConnectedEv")
    c.disconnect_error = RuntimeError("x")
    assert await pair(c, io.StringIO(), wait_seconds=2) is True


# ---- run_pair (síncrono: usa asyncio.run) --------------------------------------------------


def test_run_pair_success(wsp_home: Path) -> None:
    out = io.StringIO()
    c = client_that("ConnectedEv", qr=b"2@qr")
    assert run_pair(None, lambda: c, out) == 0
    assert out.getvalue().rstrip().endswith("Vinculado.")
    with InstanceLock():  # lock liberado
        pass


def test_run_pair_strips_plus_from_phone(wsp_home: Path) -> None:
    c = client_that("ConnectedEv")
    assert run_pair("+5215512345678", lambda: c, io.StringIO()) == 0
    assert c.phones == ["5215512345678"]


def test_run_pair_secures_session_files(wsp_home: Path) -> None:
    session = wsp_home / "data" / "session.db"
    session.write_text("x")
    session.chmod(0o644)
    c = client_that("ConnectedEv")
    assert run_pair(None, lambda: c, io.StringIO()) == 0
    assert session.stat().st_mode & 0o777 == 0o600


def test_run_pair_timeout_returns_1(
    wsp_home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = pair_mod.pair

    async def quick(client: WaClient, out: io.StringIO, phone: str | None = None) -> bool:
        return await real(client, out, phone, wait_seconds=0.05)

    monkeypatch.setattr(pair_mod, "pair", quick)
    assert run_pair(None, FakeClient, io.StringIO()) == 1
    assert "tiempo agotado" in capsys.readouterr().err


def test_run_pair_with_lock_held_returns_2(
    wsp_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def factory() -> WaClient:
        raise AssertionError("no debe crear el cliente")

    with InstanceLock():
        assert run_pair(None, factory, io.StringIO()) == 2
    assert "wspd" in capsys.readouterr().err


@pytest.mark.parametrize("phone", ["abc", "123", "+12", "1234567890123456", "55 1234 5678", ""])
def test_run_pair_invalid_phone_returns_1(
    phone: str, wsp_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def factory() -> WaClient:
        raise AssertionError("no debe crear el cliente")

    assert run_pair(phone, factory, io.StringIO()) == 1
    assert "--phone" in capsys.readouterr().err
