"""Menciones `@nombre`: resolución contra miembros y `prepare`."""

import pytest

from wsp.cli import mentions
from wsp.cli.mentions import (
    MentionError,
    Prepared,
    has_mention_token,
    prepare,
    resolve_mentions,
)

GROUP = "999@g.us"
ANA = "5215511110001@s.whatsapp.net"
LUIS = "5215522220002@s.whatsapp.net"
JOSE = "5215533330003@s.whatsapp.net"
MEMBERS = [(ANA, "Ana Pérez"), (LUIS, "Luis Gómez"), (JOSE, "José Ruiz"), ("77@lid", None)]


def test_exact_name_word() -> None:
    r = resolve_mentions("hola @Ana", MEMBERS)
    assert r == Prepared("hola @5215511110001", [ANA])


def test_prefix_match() -> None:
    assert resolve_mentions("@lu ven", MEMBERS) == Prepared("@5215522220002 ven", [LUIS])


def test_accent_and_case_insensitive() -> None:
    assert resolve_mentions("@JOSE y @perez", MEMBERS).mentions == [JOSE, ANA]
    assert resolve_mentions("@gomez", MEMBERS).mentions == [LUIS]


def test_number_by_suffix() -> None:
    assert resolve_mentions("@0003 hola", MEMBERS) == Prepared("@5215533330003 hola", [JOSE])


def test_no_match_stays_literal() -> None:
    r = resolve_mentions("hola @nadie", MEMBERS)
    assert r == Prepared("hola @nadie", [])


def test_ambiguous_raises() -> None:
    members = [(ANA, "Ana Pérez"), (LUIS, "Ana López")]
    with pytest.raises(MentionError, match="ambiguo"):
        resolve_mentions("@ana", members)


def test_exact_wins_over_prefix() -> None:
    members = [(ANA, "Ana"), (LUIS, "Anabel")]
    assert resolve_mentions("@ana", members).mentions == [ANA]


def test_duplicates_do_not_repeat_jid() -> None:
    r = resolve_mentions("@ana y @Ana otra vez", MEMBERS)
    assert r.mentions == [ANA]
    assert r.text == "@5215511110001 y @5215511110001 otra vez"


def test_double_at_and_email_not_mentions() -> None:
    assert resolve_mentions("@@ana", MEMBERS) == Prepared("@@ana", [])
    assert resolve_mentions("mail a@ana.com", MEMBERS) == Prepared("mail a@ana.com", [])


def test_member_without_name_ignored_by_name() -> None:
    assert resolve_mentions("@77", MEMBERS).mentions == ["77@lid"]  # por número
    assert resolve_mentions("@zzz", MEMBERS).mentions == []


def test_has_mention_token() -> None:
    assert has_mention_token("hola @ana")
    assert has_mention_token("@ana")
    assert not has_mention_token("sin menciones")
    assert not has_mention_token("a@b.com")
    assert not has_mention_token("@@x")


async def _no_fetch(jid: str) -> list[tuple[str, str | None]] | None:
    raise AssertionError("no debía llamarse")


async def test_prepare_non_group_skips_fetch() -> None:
    r = await prepare("hola @ana", "1@s.whatsapp.net", _no_fetch)
    assert r == Prepared("hola @ana", [])


async def test_prepare_without_token_skips_fetch() -> None:
    assert await prepare("hola", GROUP, _no_fetch) == Prepared("hola", [])


async def test_prepare_group_resolves() -> None:
    calls: list[str] = []

    async def fetch(jid: str) -> list[tuple[str, str | None]] | None:
        calls.append(jid)
        return MEMBERS

    r = await prepare("hola @ana", GROUP, fetch)
    assert calls == [GROUP]
    assert r.mentions == [ANA]


async def test_prepare_fetch_none_raises() -> None:
    async def fetch(jid: str) -> list[tuple[str, str | None]] | None:
        return None

    with pytest.raises(MentionError, match="miembros"):
        await prepare("hola @ana", GROUP, fetch)


def test_ambiguity_error_strips_escape_sequences() -> None:
    members = [("111@s.whatsapp.net", "Ana\x1b[2J"), ("222@s.whatsapp.net", "Ana\x07 B")]
    with pytest.raises(mentions.MentionError) as e:
        mentions.resolve_mentions("hola @ana", members)
    assert "\x1b" not in str(e.value)
    assert "\x07" not in str(e.value)
