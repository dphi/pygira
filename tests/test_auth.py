"""Tests for shared iscwebservice session authentication."""

import json

import pytest

from pygira.api import ApiClient
from pygira.auth import compute_session_token
from pygira.exceptions import AuthenticationError
from tests import _httpmock as respx
from tests._httpmock import Response


def test_session_token_matches_confirmed_gds1_vector() -> None:
    assert compute_session_token("secret", "A1", "B2", "GDS_1") == (
        "2CF738B22A5F58E22856BEA79AC5B31A860FD0B0D33FFD1BE72F10B6F07AB948"
    )


def test_session_token_matches_legacy_vector() -> None:
    assert compute_session_token("secret", "A1", "B2", "1") == (
        "296FD5880468C677D8AFD333BB285C2B1F4664EFC7F93D331C46C0A62C557FE5"
    )


@respx.mock
def test_shared_authenticator_sends_computed_token() -> None:
    host = "192.0.2.1"
    route = respx.post(f"http://{host}/api").mock(
        side_effect=[
            Response(200, json={"error": "ERR_COMMUNICATION", "id": "235"}),
            Response(
                200,
                json={"data": {"salt": "A1", "sessionSalt": "B2", "version": "GDS_1"}},
            ),
            Response(200, json={}),
            Response(200, json={"data": {"state": "ok"}}),
        ],
    )

    ApiClient(host, "device", "secret").check_online_update()

    auth_payload = json.loads(route.calls[2].request.read())
    assert auth_payload["data"]["token"] == (
        "2CF738B22A5F58E22856BEA79AC5B31A860FD0B0D33FFD1BE72F10B6F07AB948"
    )


@respx.mock
def test_shared_authenticator_rejects_incomplete_salt_response() -> None:
    host = "192.0.2.1"
    respx.post(f"http://{host}/api").mock(
        side_effect=[
            Response(200, json={"error": "ERR_COMMUNICATION", "id": "235"}),
            Response(200, json={"data": {"salt": "A1"}}),
        ],
    )

    with pytest.raises(AuthenticationError):
        ApiClient(host, "device", "secret").check_online_update()
