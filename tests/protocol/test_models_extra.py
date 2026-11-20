import pytest
from pydantic import ValidationError

from wsp.protocol.models import Envelope, MessageNew, ProtocolError, encode


def test_ts_rejects_above_int64() -> None:
    MessageNew(chat_jid="c", wa_id="w", ts=2**63 - 1)
    with pytest.raises(ValidationError):
        MessageNew(chat_jid="c", wa_id="w", ts=2**63)


def test_ts_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        MessageNew(chat_jid="c", wa_id="w", ts=-1)


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        "\ud800",
    ],
)
def test_encode_bad_data_is_bad_encoding(value: object) -> None:
    env = Envelope(type="x", data={"k": value})
    with pytest.raises(ProtocolError) as exc:
        encode(env)
    assert exc.value.code == "bad_encoding"
    assert "ud800" not in exc.value.message


def test_chat_updated_alias_push_name_roundtrip() -> None:
    from wsp.protocol.models import ChatUpdated

    ev = ChatUpdated(jid="1@s.whatsapp.net", kind="direct", alias="Ana", push_name="Anita")
    assert ChatUpdated.model_validate_json(ev.model_dump_json()) == ev
    legacy = ChatUpdated.model_validate({"jid": "1@s.whatsapp.net", "kind": "direct"})
    assert legacy.alias is None and legacy.push_name is None
    with pytest.raises(ValidationError):
        ChatUpdated(jid="1@s.whatsapp.net", kind="direct", alias="x" * 257)
