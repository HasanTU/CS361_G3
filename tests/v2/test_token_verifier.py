# Feature: backend-v2-auth-bypass-fix, task 3.2 (CognitoTokenVerifier)
import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from conftest import (
    TEST_CLIENT_ID,
    TEST_ISSUER,
    TEST_KID,
    TEST_POOL_ID,
    ConnectionErrorSigningKeyProvider,
    StaticSigningKeyProvider,
    build_verifier,
    default_claims,
    make_none_token,
    make_token,
)
from cryptography.hazmat.primitives import serialization

from backend_v2.shared.auth.errors import (
    AUTH_REQUIRED_MESSAGE,
    INVALID_TOKEN_MESSAGE,
    SERVICE_UNAVAILABLE_MESSAGE,
    AuthenticationError,
    AuthServiceUnavailableError,
)
from backend_v2.shared.auth.token_verifier import CognitoTokenVerifier


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _assert_invalid(verifier, token):
    with pytest.raises(AuthenticationError) as exc_info:
        verifier.verify(token)
    assert str(exc_info.value) == INVALID_TOKEN_MESSAGE
    return exc_info.value


def test_valid_token_returns_claims(verifier):
    token = make_token({"sub": "abc", "email": "a@tu.ac.th", "cognito:groups": ["ADMIN"]})
    claims = verifier.verify(token)
    assert claims["sub"] == "abc"
    assert claims["email"] == "a@tu.ac.th"
    assert claims["cognito:groups"] == ["ADMIN"]
    assert claims["token_use"] == "id"


def test_issuer_and_jwks_url_derived_from_pool_id(verifier):
    assert verifier.issuer == TEST_ISSUER
    assert verifier.jwks_url == f"{TEST_ISSUER}/.well-known/jwks.json"


def test_wrong_signing_key_rejected(verifier, other_private_key):
    _assert_invalid(verifier, make_token(key=other_private_key))


def test_alg_none_rejected_without_key_lookup():
    verifier = CognitoTokenVerifier(TEST_POOL_ID, TEST_CLIENT_ID, signing_key_provider=ConnectionErrorSigningKeyProvider())
    # A connection-error provider would raise 503 if it were reached.
    _assert_invalid(verifier, make_none_token({"sub": "x", "cognito:groups": ["ADMIN"]}))


def test_hs256_signed_with_public_key_bytes_rejected(verifier, rsa_private_key):
    secret = rsa_private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": TEST_KID}).encode())
    payload = _b64url(
        json.dumps(
            {
                "sub": "x",
                "iss": TEST_ISSUER,
                "aud": TEST_CLIENT_ID,
                "token_use": "id",
                "iat": now,
                "exp": now + 3600,
                "cognito:groups": ["ADMIN"],
            }
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = _b64url(hmac.new(secret, signing_input, hashlib.sha256).digest())
    _assert_invalid(verifier, f"{header}.{payload}.{signature}")


def test_missing_kid_rejected(verifier, rsa_private_key):
    token = jwt.encode(default_claims(), rsa_private_key, algorithm="RS256")
    _assert_invalid(verifier, token)


def test_unknown_kid_rejected(verifier):
    _assert_invalid(verifier, make_token(kid="some-other-kid"))


def test_expired_token_rejected(verifier):
    now = int(time.time())
    _assert_invalid(verifier, make_token({"iat": now - 7200, "exp": now - 3600}))


def test_iat_in_future_beyond_leeway_rejected(verifier):
    now = int(time.time())
    _assert_invalid(verifier, make_token({"iat": now + 3600, "exp": now + 7200}))


def test_expired_within_leeway_accepted(verifier):
    now = int(time.time())
    claims = verifier.verify(make_token({"iat": now - 3600, "exp": now - 3}))
    assert claims["sub"]


def test_wrong_audience_rejected(verifier):
    _assert_invalid(verifier, make_token({"aud": "other-client"}))


def test_wrong_issuer_rejected(verifier):
    _assert_invalid(
        verifier,
        make_token({"iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OtherPool"}),
    )


def test_access_token_rejected(verifier):
    _assert_invalid(verifier, make_token({"token_use": "access"}))


def test_missing_token_use_rejected(verifier):
    _assert_invalid(verifier, make_token({"token_use": None}))


def test_missing_sub_rejected(verifier):
    _assert_invalid(verifier, make_token({"sub": None}))


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b.c", "....", "\u0e01\u0e02"])
def test_malformed_token_rejected(verifier, token):
    _assert_invalid(verifier, token)


@pytest.mark.parametrize("token", ["", None, 123])
def test_empty_or_non_string_token_is_auth_required(verifier, token):
    with pytest.raises(AuthenticationError) as exc_info:
        verifier.verify(token)
    assert str(exc_info.value) == AUTH_REQUIRED_MESSAGE


def test_jwks_connection_error_is_service_unavailable():
    verifier = CognitoTokenVerifier(
        TEST_POOL_ID, TEST_CLIENT_ID, signing_key_provider=ConnectionErrorSigningKeyProvider()
    )
    with pytest.raises(AuthServiceUnavailableError) as exc_info:
        verifier.verify(make_token())
    assert str(exc_info.value) == SERVICE_UNAVAILABLE_MESSAGE


def test_rejection_hides_library_detail_and_chain(verifier):
    err = _assert_invalid(verifier, make_token({"aud": "other-client"}))
    assert err.__cause__ is None
    assert err.__suppress_context__ is True


@pytest.mark.parametrize("pool_id", ["", None, "nounderscore", "_abc", "us-east-1_"])
def test_malformed_pool_id_raises_runtime_error(pool_id):
    with pytest.raises(RuntimeError) as exc_info:
        CognitoTokenVerifier(pool_id, TEST_CLIENT_ID, signing_key_provider=StaticSigningKeyProvider({}))
    assert str(exc_info.value) == "COGNITO_USER_POOL_ID is missing or malformed"


@pytest.mark.parametrize("client_id", ["", None])
def test_empty_client_id_raises_runtime_error(client_id):
    with pytest.raises(RuntimeError) as exc_info:
        CognitoTokenVerifier(TEST_POOL_ID, client_id, signing_key_provider=StaticSigningKeyProvider({}))
    assert str(exc_info.value) == "COGNITO_CLIENT_ID is missing"


def test_from_env_uses_env_vars_and_default_key_client(monkeypatch):
    monkeypatch.setenv("COGNITO_USER_POOL_ID", TEST_POOL_ID)
    monkeypatch.setenv("COGNITO_CLIENT_ID", TEST_CLIENT_ID)
    verifier = CognitoTokenVerifier.from_env()
    assert verifier.issuer == TEST_ISSUER
    assert isinstance(verifier._keys, jwt.PyJWKClient)


def test_from_env_without_vars_raises_runtime_error(monkeypatch):
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("COGNITO_CLIENT_ID", raising=False)
    with pytest.raises(RuntimeError):
        CognitoTokenVerifier.from_env()


def test_build_verifier_helper_matches_fixture(verifier):
    assert build_verifier().issuer == verifier.issuer
