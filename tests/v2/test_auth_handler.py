# Feature: backend-v2-auth-bypass-fix, auth handler (HTTP status and message table, auth rows)
import json
import logging

import pytest

from backend_v2.functions.auth import handler as auth_handler
from backend_v2.functions.auth.use_cases import INVALID_CREDENTIAL_CODES, AuthUseCases
from backend_v2.shared.auth import set_token_verifier

from conftest import (
    SEED_BY_EMAIL,
    SEED_USERS,
    ConnectionErrorSigningKeyProvider,
    FakeCognitoClient,
    FakeUserDirectory,
    SpyUseCases,
    body_of,
    build_verifier,
    describe,
    error_message,
    make_event,
    make_none_token,
    make_token,
    make_user_token,
)

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
USERS = "/api/v1/auth/users"
ME = "/api/v1/auth/me"

AUTH_REQUIRED = "Authentication is required"
INVALID_TOKEN = "Invalid authentication token"
UNAVAILABLE = "Authentication service is unavailable"
BAD_CREDENTIALS = "Invalid email or password"
INTERNAL = "Internal server error"


def login_event(email="admin1@tu.ac.th", password="secret-pw"):
    return make_event("POST", LOGIN, body={"email": email, "password": password})


def assert_error(response, status, message):
    assert response["statusCode"] == status, describe(response)
    assert body_of(response) == {"error": {"message": message}}, describe(response)


@pytest.fixture
def auth_with_directory():
    """Auth lambda with a user directory and no Cognito client."""
    auth_handler.set_use_cases(AuthUseCases(user_directory=FakeUserDirectory()))
    try:
        yield auth_handler.lambda_handler
    finally:
        auth_handler.set_use_cases(None)  # type: ignore[arg-type]


@pytest.fixture
def spy_auth():
    spy = SpyUseCases()
    auth_handler.set_use_cases(spy)  # type: ignore[arg-type]
    try:
        yield spy
    finally:
        auth_handler.set_use_cases(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def test_login_success_returns_user_token_and_cookie(auth_login):
    token = make_user_token("admin1@tu.ac.th")
    handler = auth_login(FakeCognitoClient(id_token=token))

    response = handler(login_event())

    assert response["statusCode"] == 200, describe(response)
    body = body_of(response)
    assert set(body) == {"user", "token"}
    assert body["token"] == token
    user = body["user"]
    assert list(user) == ["id", "email", "name", "role", "faculty"]
    assert user["role"] == "admin"
    assert user["id"] == SEED_BY_EMAIL["admin1@tu.ac.th"]["sub"]
    cookie = f"auth_token={token}; Path=/; HttpOnly; Secure; SameSite=None; Max-Age=86400"
    assert response["headers"]["Set-Cookie"] == cookie
    assert response["cookies"] == [cookie]


def test_login_user_group_gets_user_role(auth_login):
    handler = auth_login(FakeCognitoClient(id_token=make_user_token("user1@tu.ac.th")))
    response = handler(login_event("user1@tu.ac.th"))
    assert response["statusCode"] == 200, describe(response)
    assert body_of(response)["user"]["role"] == "user"


@pytest.mark.parametrize("code", sorted(INVALID_CREDENTIAL_CODES))
def test_login_invalid_credential_codes_give_401(auth_login, code):
    handler = auth_login(FakeCognitoClient(error=FakeCognitoClient.client_error(code)))
    response = handler(login_event())
    assert_error(response, 401, BAD_CREDENTIALS)
    assert "Set-Cookie" not in response["headers"]
    assert "cookies" not in response


def test_login_other_client_error_gives_503(auth_login):
    handler = auth_login(
        FakeCognitoClient(error=FakeCognitoClient.client_error("InternalErrorException"))
    )
    response = handler(login_event())
    assert_error(response, 503, UNAVAILABLE)
    assert "Set-Cookie" not in response["headers"]
    assert "cookies" not in response


def test_login_cognito_unreachable_gives_503_without_token_or_cookie(auth_login):
    handler = auth_login(FakeCognitoClient(error=FakeCognitoClient.connection_error()))
    response = handler(login_event("admin@kanna.ac.th", "admin1234"))
    assert_error(response, 503, UNAVAILABLE)
    assert "token" not in body_of(response)
    assert "Set-Cookie" not in response["headers"]
    assert "cookies" not in response


def test_login_challenge_without_authentication_result_gives_401(auth_login):
    class ChallengeClient:
        def initiate_auth(self, **kwargs):
            return {"ChallengeName": "NEW_PASSWORD_REQUIRED", "Session": "abc"}

    handler = auth_login(ChallengeClient())
    response = handler(login_event())
    assert_error(response, 401, BAD_CREDENTIALS)
    assert "Set-Cookie" not in response["headers"]


def test_login_token_failing_verification_gives_503(auth_login, other_private_key):
    forged = make_user_token("admin1@tu.ac.th", key=other_private_key)
    handler = auth_login(FakeCognitoClient(id_token=forged))
    response = handler(login_event())
    assert_error(response, 503, UNAVAILABLE)
    assert "token" not in body_of(response)
    assert "Set-Cookie" not in response["headers"]


def test_login_without_client_id_gives_503(monkeypatch):
    monkeypatch.delenv("COGNITO_CLIENT_ID", raising=False)
    auth_handler.set_use_cases(AuthUseCases(cognito_client=FakeCognitoClient(id_token="x")))
    try:
        response = auth_handler.lambda_handler(login_event())
    finally:
        auth_handler.set_use_cases(None)  # type: ignore[arg-type]
    assert_error(response, 503, UNAVAILABLE)


@pytest.mark.parametrize(
    "email,password",
    [("", "pw"), ("a@b.c", ""), ("   ", "pw"), ("", "")],
)
def test_login_empty_email_or_password_gives_400(auth_login, email, password):
    client = FakeCognitoClient(id_token=make_user_token("admin1@tu.ac.th"))
    handler = auth_login(client)
    response = handler(login_event(email, password))
    assert_error(response, 400, "Email and password are required")
    assert client.calls == []


def test_login_does_not_require_a_token(auth_login):
    handler = auth_login(FakeCognitoClient(id_token=make_user_token("user2@tu.ac.th")))
    event = login_event("user2@tu.ac.th")
    assert event["headers"] == {} and event["cookies"] == []
    assert handler(event)["statusCode"] == 200


# ---------------------------------------------------------------------------
# Logout and OPTIONS
# ---------------------------------------------------------------------------

def test_logout_needs_no_token(auth_login):
    handler = auth_login(FakeCognitoClient())
    response = handler(make_event("POST", LOGOUT))
    clear = "auth_token=; Path=/; HttpOnly; Secure; SameSite=None; Max-Age=0"
    assert response["statusCode"] == 200
    assert body_of(response) == {"message": "Logged out successfully"}
    assert response["headers"]["Set-Cookie"] == clear
    assert response["cookies"] == [clear]


def test_options_returns_204_before_authentication(spy_auth):
    response = auth_handler.lambda_handler(make_event("OPTIONS", USERS))
    assert response["statusCode"] == 204
    assert response["body"] == ""
    assert response["headers"]["Access-Control-Allow-Methods"] == "GET,POST,OPTIONS"
    assert spy_auth.calls == []


# ---------------------------------------------------------------------------
# GET /auth/users
# ---------------------------------------------------------------------------

def test_users_without_token_gives_401(spy_auth):
    response = auth_handler.lambda_handler(make_event("GET", USERS))
    assert_error(response, 401, AUTH_REQUIRED)
    assert spy_auth.calls == []


def test_users_with_none_token_gives_401(spy_auth):
    token = make_none_token({"sub": "attacker", "cognito:groups": ["ADMIN"]})
    response = auth_handler.lambda_handler(make_event("GET", USERS, token=token))
    assert_error(response, 401, INVALID_TOKEN)
    assert spy_auth.calls == []


@pytest.mark.parametrize("via", ["bearer", "cookies", "cookie_header"])
def test_users_for_user_caller_returns_id_and_name(auth_with_directory, via):
    token = make_user_token("user1@tu.ac.th")
    response = auth_with_directory(make_event("GET", USERS, token=token, via=via))

    assert response["statusCode"] == 200, describe(response)
    users = body_of(response)["users"]
    assert len(users) == len(SEED_USERS)
    assert all(set(u) == {"id", "name"} for u in users)
    assert {(u["id"], u["name"]) for u in users} == {(u["sub"], u["name"]) for u in SEED_USERS}


def test_users_for_admin_caller_also_allowed(auth_with_directory):
    token = make_user_token("admin1@tu.ac.th")
    response = auth_with_directory(make_event("GET", USERS, token=token))
    assert response["statusCode"] == 200, describe(response)


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------

def test_me_without_token_gives_401(spy_auth):
    assert_error(auth_handler.lambda_handler(make_event("GET", ME)), 401, AUTH_REQUIRED)


def test_me_with_none_token_gives_401(spy_auth):
    token = make_none_token({"sub": "attacker", "cognito:groups": ["ADMIN"]})
    response = auth_handler.lambda_handler(make_event("GET", ME, token=token))
    assert_error(response, 401, INVALID_TOKEN)


def test_me_with_foreign_key_gives_401(spy_auth, other_private_key):
    token = make_user_token("admin1@tu.ac.th", key=other_private_key)
    response = auth_handler.lambda_handler(make_event("GET", ME, token=token))
    assert_error(response, 401, INVALID_TOKEN)


def test_me_returns_verified_claims(spy_auth):
    token = make_user_token("fac_a@tu.ac.th", {"custom:role": "admin"})
    response = auth_handler.lambda_handler(make_event("GET", ME, token=token))

    assert response["statusCode"] == 200, describe(response)
    claims = body_of(response)["user"]
    seed = SEED_BY_EMAIL["fac_a@tu.ac.th"]
    assert claims["sub"] == seed["sub"]
    assert claims["email"] == seed["email"]
    assert claims["name"] == seed["name"]
    assert claims["cognito:groups"] == ["USER"]
    assert claims["token_use"] == "id"
    assert spy_auth.calls == []


# ---------------------------------------------------------------------------
# 503, 500 and mapping of use case exceptions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [USERS, ME])
def test_jwks_unreachable_gives_503(spy_auth, path):
    verifier = build_verifier()
    verifier._keys = ConnectionErrorSigningKeyProvider()
    set_token_verifier(verifier)
    token = make_user_token("user1@tu.ac.th")
    response = auth_handler.lambda_handler(make_event("GET", path, token=token))
    assert_error(response, 503, UNAVAILABLE)
    assert spy_auth.calls == []


def test_missing_verifier_configuration_gives_generic_500(spy_auth, monkeypatch):
    set_token_verifier(None)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("COGNITO_CLIENT_ID", raising=False)
    token = make_user_token("user1@tu.ac.th")
    response = auth_handler.lambda_handler(make_event("GET", ME, token=token))
    assert_error(response, 500, INTERNAL)


def test_unhandled_error_gives_generic_500_and_is_logged(auth_with_directory, caplog):
    class Boom(FakeUserDirectory):
        def list_users(self):
            raise RuntimeError("secret-host:27017")

    auth_handler.set_use_cases(AuthUseCases(user_directory=Boom()))
    token = make_user_token("user1@tu.ac.th")
    with caplog.at_level(logging.ERROR):
        response = auth_handler.lambda_handler(make_event("GET", USERS, token=token))

    assert_error(response, 500, INTERNAL)
    assert "secret-host" not in response["body"]
    assert any("Unhandled error in auth handler" in r.getMessage() for r in caplog.records)


def test_get_use_cases_failure_gives_generic_500(monkeypatch):
    auth_handler.set_use_cases(None)  # type: ignore[arg-type]

    def boom():
        raise RuntimeError("COGNITO_USER_POOL_ID is missing")

    monkeypatch.setattr(auth_handler, "get_use_cases", boom)
    response = auth_handler.lambda_handler(make_event("POST", LOGOUT))
    assert_error(response, 500, INTERNAL)


def test_permission_error_from_use_case_gives_401(spy_auth):
    def deny(*args, **kwargs):
        raise PermissionError("Invalid email or password")

    spy_auth.login = deny
    response = auth_handler.lambda_handler(login_event())
    assert_error(response, 401, BAD_CREDENTIALS)


def test_value_error_from_use_case_gives_400(spy_auth):
    def bad(*args, **kwargs):
        raise ValueError("bad input")

    spy_auth.login = bad
    assert_error(auth_handler.lambda_handler(login_event()), 400, "bad input")


def test_malformed_login_json_gives_400(spy_auth):
    event = make_event("POST", LOGIN, body="{not json")
    response = auth_handler.lambda_handler(event)
    assert response["statusCode"] == 400, describe(response)


def test_unknown_route_gives_404(spy_auth):
    response = auth_handler.lambda_handler(make_event("GET", "/api/v1/auth/nope"))
    assert_error(response, 404, "Route not found: GET /api/v1/auth/nope")


def test_wrong_audience_token_gives_401_on_me(spy_auth):
    token = make_token({"aud": "other-client"})
    response = auth_handler.lambda_handler(make_event("GET", ME, token=token))
    assert_error(response, 401, INVALID_TOKEN)
    json.loads(response["body"])
