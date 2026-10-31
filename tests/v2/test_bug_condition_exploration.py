# Feature: backend-v2-auth-bypass-fix, Property 1
"""Bug condition exploration: only verified Cognito identities and groups authorize requests.

Every test asserts the behavior AFTER the fix. On the unfixed code (commit b52f483)
every test in this file fails with an assertion error. The file only uses
lambda_handler, set_use_cases(), the use case classes and the conftest helpers,
so it runs unchanged before and after the fix.
"""
import logging
import time

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from conftest import (
    SEED_BY_EMAIL,
    SpyUseCases,
    default_claims,
    describe,
    error_message,
    body_of,
    make_event,
    make_multipart,
    make_none_token,
    make_token,
    make_user_token,
    seed_document,
    FakeCognitoClient,
)

STAFF = SEED_BY_EMAIL["staff1@tu.ac.th"]
HEAD1 = SEED_BY_EMAIL["head1@tu.ac.th"]
FAC_A = SEED_BY_EMAIL["fac_a@tu.ac.th"]
FAC_B = SEED_BY_EMAIL["fac_b@tu.ac.th"]


def _has_set_cookie(response):
    headers = {k.lower(): v for k, v in (response.get("headers") or {}).items()}
    return "set-cookie" in headers or bool(response.get("cookies"))


# 1 -------------------------------------------------------------------------
def test_unsigned_admin_token_is_rejected_on_documents(app):
    seed_document(app.repo, app.storage, assigned_to=[HEAD1["sub"]], receiver=HEAD1["name"])
    token = make_none_token({"sub": "attacker", "cognito:groups": ["ADMIN"]})
    resp = app.documents(make_event("GET", "/api/v1/documents", token))
    assert resp["statusCode"] == 401, describe(resp)
    assert body_of(resp) == {"error": {"message": "Invalid authentication token"}}, describe(resp)


# 2 -------------------------------------------------------------------------
def test_foreign_expired_wrong_aud_wrong_iss_and_access_tokens_are_rejected_on_search(
    app, other_private_key
):
    seed_document(app.repo, app.storage, assigned_to=[HEAD1["sub"]], receiver=HEAD1["name"])
    now = int(time.time())
    admin = {"cognito:groups": ["ADMIN"]}
    tokens = {
        "foreign_key": make_token(admin, key=other_private_key),
        "expired": make_token({**admin, "iat": now - 7200, "exp": now - 3600}),
        "wrong_aud": make_token({**admin, "aud": "other-client"}),
        "wrong_iss": make_token(
            {**admin, "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OtherPool"}
        ),
        "access_token": make_token({**admin, "token_use": "access"}),
    }
    accepted = {}
    for label, token in tokens.items():
        resp = app.search(make_event("GET", "/api/v1/search/trie", token))
        if resp["statusCode"] != 401:
            accepted[label] = resp["statusCode"]
    assert not accepted, f"tokens accepted instead of 401: {accepted}"


# 3 -------------------------------------------------------------------------
def test_display_name_staff_does_not_grant_admin_for_registration(app):
    token = make_token(
        {
            "sub": "11111111-1111-4111-8111-111111111111",
            "email": "helper@example.com",
            "name": "Staff Helper",
            "cognito:groups": ["USER"],
        }
    )
    content_type, body = make_multipart(
        {
            "title": "Forged registration",
            "document_date": "2025-01-15",
            "sender": "Someone",
            "receiver": HEAD1["name"],
        }
    )
    event = make_event(
        "POST", "/api/v1/documents", token, body=body, headers={"content-type": content_type}
    )
    resp = app.documents(event)
    assert resp["statusCode"] == 403, describe(resp)
    assert error_message(resp) == "Admin privilege required to register documents", describe(resp)


# 4 -------------------------------------------------------------------------
def test_custom_role_admin_claim_does_not_grant_access_to_other_users_documents(app):
    mine = seed_document(app.repo, app.storage, assigned_to=[FAC_A["sub"]], receiver=FAC_A["name"])
    other = seed_document(
        app.repo, app.storage, assigned_to=[HEAD1["sub"]], receiver=HEAD1["name"], title="Other"
    )
    token = make_user_token("fac_a@tu.ac.th", {"custom:role": "admin"})
    resp = app.documents(make_event("GET", "/api/v1/documents", token))
    assert resp["statusCode"] == 200, describe(resp)
    ids = [d["id"] for d in body_of(resp)["documents"]]
    assert other.id not in ids, f"non-admin saw another user's document; ids={ids}"
    assert ids == [mine.id], f"ids={ids}"


# 5 -------------------------------------------------------------------------
def test_copying_another_users_display_name_does_not_grant_their_file(app):
    doc = seed_document(
        app.repo, app.storage, assigned_to=[FAC_B["sub"]], receiver=FAC_B["name"]
    )
    token = make_user_token("fac_a@tu.ac.th", {"name": FAC_B["name"]})
    resp = app.documents(make_event("GET", f"/api/v1/documents/{doc.id}/file", token))
    assert resp["statusCode"] == 403, describe(resp)
    assert error_message(resp) == "Access denied: You are not assigned to this document", describe(resp)


# 6 -------------------------------------------------------------------------
def test_login_fails_closed_when_cognito_is_unreachable(auth_login):
    client = FakeCognitoClient(error=FakeCognitoClient.connection_error())
    handler = auth_login(client)
    event = make_event(
        "POST", "/api/v1/auth/login", body={"email": "admin@kanna.ac.th", "password": "admin1234"}
    )
    resp = handler(event)
    assert resp["statusCode"] == 503, describe(resp)
    assert error_message(resp) == "Authentication service is unavailable", describe(resp)
    assert "token" not in (body_of(resp) or {}), describe(resp)
    assert not _has_set_cookie(resp), f"cookie set: {resp.get('headers')}"


# 7 -------------------------------------------------------------------------
def test_login_role_comes_only_from_cognito_groups(auth_login):
    token = make_token(
        {
            "sub": "22222222-2222-4222-8222-222222222222",
            "email": "admin9@tu.ac.th",
            "name": "Admin Nine",
            "cognito:groups": ["USER"],
        }
    )
    handler = auth_login(FakeCognitoClient(id_token=token))
    event = make_event(
        "POST", "/api/v1/auth/login", body={"email": "admin9@tu.ac.th", "password": "Whatever1!"}
    )
    resp = handler(event)
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp)["user"]["role"] == "user", describe(resp)


# 8 -------------------------------------------------------------------------
def test_auth_users_requires_authentication(auth_login):
    handler = auth_login(FakeCognitoClient())
    resp = handler(make_event("GET", "/api/v1/auth/users"))
    assert resp["statusCode"] == 401, describe(resp)
    assert error_message(resp) == "Authentication is required", describe(resp)


# 9 -------------------------------------------------------------------------
def test_auth_me_rejects_unsigned_token(auth_login):
    handler = auth_login(FakeCognitoClient())
    token = make_none_token({"sub": "attacker", "cognito:groups": ["ADMIN"]})
    resp = handler(make_event("GET", "/api/v1/auth/me", token))
    assert resp["statusCode"] == 401, describe(resp)
    assert error_message(resp) == "Invalid authentication token", describe(resp)


# 10 ------------------------------------------------------------------------
def test_unhandled_error_returns_generic_500(app):
    app.repo.raise_on_find_all = RuntimeError("secret-host:27017")
    token = make_user_token("staff1@tu.ac.th")
    resp = app.documents(make_event("GET", "/api/v1/documents", token))
    assert resp["statusCode"] == 500, describe(resp)
    assert body_of(resp) == {"error": {"message": "Internal server error"}}, describe(resp)


# 11 ------------------------------------------------------------------------
def test_authenticated_request_writes_no_identity_to_stdout_or_logs(app, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    seed_document(app.repo, app.storage, assigned_to=[HEAD1["sub"]], receiver=HEAD1["name"])
    token = make_user_token("head1@tu.ac.th")
    resp = app.documents(make_event("GET", "/api/v1/documents", token))
    assert resp["statusCode"] == 200, describe(resp)
    captured = capsys.readouterr()
    output = captured.out + captured.err + caplog.text
    assert "[AUTH_DEBUG]" not in output, f"identity debug line written: {output[:300]}"
    assert HEAD1["email"] not in output, f"caller email written: {output[:300]}"


# 12 ------------------------------------------------------------------------
DEFECTS = ["unsigned", "foreign_key", "expired", "wrong_aud", "wrong_iss", "access_token"]

claims_strategy = st.fixed_dictionaries(
    {
        "sub": st.uuids().map(str),
        "email": st.emails(),
        "name": st.text(min_size=1, max_size=30),
        "cognito:groups": st.lists(
            st.sampled_from(["ADMIN", "USER", "STAFF", "admin"]), unique=True, max_size=4
        ),
        "custom:role": st.one_of(st.none(), st.sampled_from(["admin", "ADMIN", "user"])),
    }
)


def _defective_token(defect, claims, other_key):
    now = int(time.time())
    if defect == "unsigned":
        return make_none_token(default_claims(claims))
    if defect == "foreign_key":
        return make_token(claims, key=other_key)
    if defect == "expired":
        return make_token({**claims, "iat": now - 7200, "exp": now - 3600})
    if defect == "wrong_aud":
        return make_token({**claims, "aud": "other-client"})
    if defect == "wrong_iss":
        return make_token(
            {**claims, "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OtherPool"}
        )
    if defect == "access_token":
        return make_token({**claims, "token_use": "access"})
    raise AssertionError(defect)


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    claims=claims_strategy,
    defect=st.sampled_from(DEFECTS),
    via=st.sampled_from(["bearer", "cookies", "cookie_header"]),
)
def test_any_token_defect_is_rejected_on_every_documents_and_search_route(
    other_private_key, claims, defect, via
):
    from backend_v2.functions.documents import handler as documents_handler
    from backend_v2.functions.search import handler as search_handler

    spy = SpyUseCases()
    documents_handler.set_use_cases(spy)
    search_handler.set_use_cases(spy)
    try:
        token = _defective_token(defect, claims, other_private_key)
        content_type, multipart_body = make_multipart(
            {"title": "t", "document_date": "2025-01-15", "sender": "s", "receiver": "r"}
        )
        documents_routes = [
            make_event("GET", "/api/v1/documents", token, via),
            make_event(
                "POST", "/api/v1/documents", token, via,
                body=multipart_body, headers={"content-type": content_type},
            ),
            make_event("GET", "/api/v1/documents/doc-1", token, via),
            make_event("GET", "/api/v1/documents/doc-1/file", token, via),
            make_event("POST", "/api/v1/documents/doc-1/assign", token, via, body={"recipients": ["x"]}),
            make_event("PATCH", "/api/v1/documents/doc-1/status", token, via, body={"status": "ACKNOWLEDGED"}),
        ]
        responses = [
            (
                f'{e["requestContext"]["http"]["method"]} {e["rawPath"]}',
                documents_handler.lambda_handler(e),
            )
            for e in documents_routes
        ]
        responses.append(
            (
                "GET /api/v1/search/trie",
                search_handler.lambda_handler(make_event("GET", "/api/v1/search/trie", token, via)),
            )
        )
        not_rejected = {
            label: describe(resp) for label, resp in responses if resp["statusCode"] != 401
        }
        assert not not_rejected, f"defect={defect} via={via} not rejected with 401: {not_rejected}"
        assert spy.calls == [], f"use cases ran: {spy.calls}"
    finally:
        documents_handler.set_use_cases(None)
        search_handler.set_use_cases(None)
