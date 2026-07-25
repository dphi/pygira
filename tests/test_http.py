"""Tests for the stdlib HTTP transport security options."""

import hashlib
import ssl
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pygira import _http
from pygira.config_service import TlsConfig, _make_client
from pygira.exceptions import InvalidInputError, TransportError

HTTP_NOT_FOUND = 404


def _response_with_certificate(certificate: bytes) -> object:
    sock = MagicMock()
    sock.getpeercert.return_value = certificate
    return SimpleNamespace(fp=SimpleNamespace(raw=SimpleNamespace(_sock=sock)))


def test_http_client_accepts_colon_separated_sha256_pin() -> None:
    certificate = b"sanitized test certificate"
    digest = hashlib.sha256(certificate).hexdigest()
    formatted = "sha256:" + ":".join(digest[index : index + 2] for index in range(0, 64, 2))
    client = _http.Client(certificate_fingerprint=formatted)

    client._validate_peer_certificate(_response_with_certificate(certificate))


def test_http_client_rejects_mismatched_certificate_pin() -> None:
    client = _http.Client(certificate_fingerprint="00" * 32)

    with pytest.raises(TransportError, match="fingerprint mismatch"):
        client._validate_peer_certificate(_response_with_certificate(b"different certificate"))


def test_http_client_rejects_invalid_certificate_pin() -> None:
    with pytest.raises(InvalidInputError, match="SHA-256"):
        _http.Client(certificate_fingerprint="not-a-fingerprint")


def test_http_error_preserves_response_status() -> None:
    response = _http.Response(HTTP_NOT_FOUND, b"")

    with pytest.raises(_http.HTTPError) as error:
        response.raise_for_status()

    assert error.value.status_code == HTTP_NOT_FOUND


def test_configuration_service_propagates_custom_tls_options() -> None:
    context = ssl.create_default_context()
    tls = TlsConfig(
        verify=True,
        ssl_context=context,
        certificate_fingerprint="11" * 32,
    )

    with patch("pygira.config_service.httpx.Client") as factory:
        _make_client("192.0.2.1", "device", "secret", tls=tls)

    assert factory.call_args.kwargs["verify"] is context
    assert factory.call_args.kwargs["certificate_fingerprint"] == "11" * 32


def test_tls_config_can_enable_system_ca_verification() -> None:
    assert TlsConfig(verify=True).verify_argument is True
