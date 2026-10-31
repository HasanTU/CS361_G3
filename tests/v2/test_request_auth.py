import pytest
from conftest import make_token

from backend_v2.shared.auth import (
    AUTH_REQUIRED_MESSAGE,
    AuthenticationError,
    authenticate,
    extract_token,
    set_token_verifier,
)


def test_bearer_header_lower_and_upper_case():
    assert extract_token({"headers": {"authorization": "Bearer abc"}}) == "abc"
    assert extract_token({"headers": {"Authorization": "Bearer  abc "}}) == "abc"


def test_cookies_array():
    event = {"cookies": ["theme=dark", "auth_token=tok1; Path=/; HttpOnly"]}
    assert extract_token(event) == "tok1"


def test_cookie_header_lower_and_upper_case():
    assert extract_token({"headers": {"cookie": "a=b; auth_token=tok2; c=d"}}) == "tok2"
    assert extract_token({"headers": {"Cookie": "auth_token=tok3"}}) == "tok3"


def test_order_bearer_then_cookies_then_cookie_header():
    event = {
        "headers": {"authorization": "Bearer h", "cookie": "auth_token=c"},
        "cookies": ["auth_token=a"],
    }
    assert extract_token(event) == "h"
    del event["headers"]["authorization"]
    assert extract_token(event) == "a"
    del event["cookies"]
    assert extract_token(event) == "c"


def test_empty_bearer_and_no_token_return_none():
    assert extract_token({"headers": {"authorization": "Bearer "}}) is None
    assert extract_token({"headers": {}}) is None
    assert extract_token({}) is None


def test_authenticate_without_token_raises_before_building_verifier(monkeypatch):
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("COGNITO_CLIENT_ID", raising=False)
    set_token_verifier(None)
    with pytest.raises(AuthenticationError) as exc:
        authenticate({"headers": {}})
    assert str(exc.value) == AUTH_REQUIRED_MESSAGE


def test_authenticate_returns_caller_from_verified_token(verifier):
    token = make_token({"sub": "sub-1", "email": "a@tu.ac.th", "cognito:groups": ["ADMIN"]})
    caller = authenticate({"headers": {"authorization": f"Bearer {token}"}}, verifier)
    assert caller.sub == "sub-1"
    assert caller.is_admin
