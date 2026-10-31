"""Documents handler: authentication, authorization and error mapping.

Covers every documents row of the HTTP status and message table in design.md.
All tests run offline with the fakes from conftest.py.
"""
import base64
import logging

import pytest

from conftest import (
    SEED_BY_EMAIL,
    ConnectionErrorSigningKeyProvider,
    FakeUserDirectory,
    SpyUseCases,
    body_of,
    build_document_use_cases,
    describe,
    error_message,
    make_event,
    make_multipart,
    make_none_token,
    make_token,
    make_user_token,
    seed_document,
)
from backend_v2.functions.documents import handler as documents_handler
from backend_v2.shared.auth import CognitoTokenVerifier, set_token_verifier

DOCS = "/api/v1/documents"

ADMIN = "staff1@tu.ac.th"
HEAD = "head1@tu.ac.th"
FAC_A = "fac_a@tu.ac.th"
FAC_B = "fac_b@tu.ac.th"

NOT_ASSIGNED = "Access denied: You are not assigned to this document"
CANNOT_UPDATE = "Access denied: You cannot update an unassigned document"
CANNOT_ASSIGN = "Permission required to assign or forward this document"
ADMIN_REQUIRED = "Admin privilege required to register documents"
AUTH_REQUIRED = "Authentication is required"
INVALID_TOKEN = "Invalid authentication token"
UNAVAILABLE = "Authentication service is unavailable"
INTERNAL = "Internal server error"


def sub_of(email):
    return SEED_BY_EMAIL[email]["sub"]


def name_of(email):
    return SEED_BY_EMAIL[email]["name"]


def token_of(email, **kwargs):
    return make_user_token(email, **kwargs)


def add_doc(app, owner_email, **kwargs):
    return seed_document(
        app.repo,
        app.storage,
        assigned_to=[sub_of(owner_email)],
        receiver=name_of(owner_email),
        **kwargs,
    )


def register_event(token, receiver, via="bearer", extra_fields=None, content_type=None):
    fields = {
        "title": "Budget letter",
        "document_date": "2025-02-01",
        "sender": "Finance Office",
        "receiver": receiver,
    }
    fields.update(extra_fields or {})
    multipart_type, body = make_multipart(fields)
    return make_event(
        "POST",
        DOCS,
        token,
        via=via,
        body=body,
        headers={"content-type": content_type or multipart_type},
    )


# ---------------------------------------------------------------------------
# 401: no token, bad token
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", DOCS),
        ("POST", DOCS),
        ("GET", f"{DOCS}/doc-1"),
        ("GET", f"{DOCS}/doc-1/file"),
        ("PATCH", f"{DOCS}/doc-1/status"),
        ("POST", f"{DOCS}/doc-1/assign"),
    ],
)
def test_no_token_is_401_and_no_use_case_runs(method, path):
    spy = SpyUseCases()
    documents_handler.set_use_cases(spy)
    try:
        resp = documents_handler.lambda_handler(make_event(method, path, body={"x": 1}))
    finally:
        documents_handler.set_use_cases(None)
    assert resp["statusCode"] == 401, describe(resp)
    assert error_message(resp) == AUTH_REQUIRED
    assert spy.calls == []


@pytest.mark.parametrize("via", ["bearer", "cookies", "cookie_header"])
@pytest.mark.parametrize(
    "kind",
    ["none", "foreign_key", "expired", "wrong_aud", "wrong_iss", "access", "garbage"],
)
def test_unverified_token_is_401_and_no_use_case_runs(kind, via, other_private_key):
    claims = {"sub": "s-1", "cognito:groups": ["ADMIN"]}
    if kind == "none":
        token = make_none_token(claims)
    elif kind == "foreign_key":
        token = make_token(claims, key=other_private_key)
    elif kind == "expired":
        token = make_token({**claims, "exp": 1_000_000_000, "iat": 999_990_000})
    elif kind == "wrong_aud":
        token = make_token({**claims, "aud": "other-client"})
    elif kind == "wrong_iss":
        token = make_token(
            {**claims, "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Other"}
        )
    elif kind == "access":
        token = make_token({**claims, "token_use": "access"})
    else:
        token = "not-a-jwt"

    spy = SpyUseCases()
    documents_handler.set_use_cases(spy)
    try:
        resp = documents_handler.lambda_handler(make_event("GET", DOCS, token, via=via))
    finally:
        documents_handler.set_use_cases(None)
    assert resp["statusCode"] == 401, describe(resp)
    assert error_message(resp) == INVALID_TOKEN
    assert spy.calls == []


# ---------------------------------------------------------------------------
# 503 and 500: auth service problems
# ---------------------------------------------------------------------------

def test_unreachable_jwks_is_503(app):
    set_token_verifier(
        CognitoTokenVerifier(
            "us-east-1_TestPool1",
            "test-client-id",
            signing_key_provider=ConnectionErrorSigningKeyProvider(),
        )
    )
    resp = app.documents(make_event("GET", DOCS, token_of(ADMIN)))
    assert resp["statusCode"] == 503, describe(resp)
    assert error_message(resp) == UNAVAILABLE


def test_missing_cognito_configuration_is_generic_500(app, monkeypatch, caplog):
    set_token_verifier(None)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("COGNITO_CLIENT_ID", raising=False)
    with caplog.at_level(logging.ERROR):
        resp = app.documents(make_event("GET", DOCS, token_of(ADMIN)))
    assert resp["statusCode"] == 500, describe(resp)
    assert body_of(resp) == {"error": {"message": INTERNAL}}


def test_unhandled_error_is_generic_500_and_logged_without_identity(app, caplog, capsys):
    app.repo.raise_on_find_all = RuntimeError("secret-host:27017")
    with caplog.at_level(logging.DEBUG):
        resp = app.documents(make_event("GET", DOCS, token_of(ADMIN)))

    assert resp["statusCode"] == 500, describe(resp)
    assert body_of(resp) == {"error": {"message": INTERNAL}}
    assert "secret-host" not in resp["body"]
    assert any("Unhandled error in documents handler" in r.getMessage() for r in caplog.records)

    captured = capsys.readouterr()
    logged = captured.out + captured.err + caplog.text
    assert ADMIN not in logged
    assert "[AUTH_DEBUG]" not in logged


def test_authenticated_request_writes_no_identity_to_stdout_or_logs(app, caplog, capsys):
    add_doc(app, HEAD)
    with caplog.at_level(logging.DEBUG):
        resp = app.documents(make_event("GET", DOCS, token_of(ADMIN)))
    assert resp["statusCode"] == 200, describe(resp)
    captured = capsys.readouterr()
    logged = captured.out + captured.err + caplog.text
    for value in (ADMIN, sub_of(ADMIN), name_of(ADMIN), "[AUTH_DEBUG]", "ADMIN"):
        assert value not in logged


# ---------------------------------------------------------------------------
# 403: roles and assignment
# ---------------------------------------------------------------------------

def test_non_admin_cannot_register_and_nothing_is_stored(app):
    resp = app.documents(register_event(token_of(HEAD), name_of(FAC_A)))
    assert resp["statusCode"] == 403, describe(resp)
    assert error_message(resp) == ADMIN_REQUIRED
    assert app.repo._docs == {}
    assert app.storage.stored_files() == []


def test_admin_check_runs_before_content_type_check(app):
    event = register_event(token_of(HEAD), name_of(FAC_A), content_type="application/json")
    resp = app.documents(event)
    assert resp["statusCode"] == 403, describe(resp)
    assert error_message(resp) == ADMIN_REQUIRED


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "Staff Helper"},
        {"email": "admin-fan@example.com"},
        {"custom:role": "admin"},
        {"role": "admin"},
        {"cognito:groups": ["admin"]},
        {"cognito:groups": ["STAFF"]},
        {"cognito:groups": ["USER"], "cognito:username": "admin1"},
    ],
)
def test_admin_is_decided_only_by_the_exact_admin_group(app, overrides):
    resp = app.documents(register_event(token_of(HEAD, claims_overrides=overrides), name_of(FAC_A)))
    assert resp["statusCode"] == 403, describe(resp)
    assert error_message(resp) == ADMIN_REQUIRED


def test_non_assigned_user_is_denied_details_file_status_and_assign(app):
    doc = add_doc(app, FAC_B)
    token = token_of(FAC_A)

    for path in (f"{DOCS}/{doc.id}", f"{DOCS}/{doc.id}/file"):
        resp = app.documents(make_event("GET", path, token))
        assert resp["statusCode"] == 403, describe(resp)
        assert error_message(resp) == NOT_ASSIGNED

    resp = app.documents(
        make_event("PATCH", f"{DOCS}/{doc.id}/status", token, body={"status": "ACKNOWLEDGED"})
    )
    assert resp["statusCode"] == 403, describe(resp)
    assert error_message(resp) == CANNOT_UPDATE

    resp = app.documents(
        make_event("POST", f"{DOCS}/{doc.id}/assign", token, body={"recipients": [sub_of(FAC_A)]})
    )
    assert resp["statusCode"] == 403, describe(resp)
    assert error_message(resp) == CANNOT_ASSIGN

    stored = app.repo.get_by_id(doc.id)
    assert stored.assigned_to == [sub_of(FAC_B)]


def test_copying_another_users_display_name_gives_no_access(app):
    doc = add_doc(app, FAC_B)
    token = token_of(FAC_A, claims_overrides={"name": name_of(FAC_B)})

    resp = app.documents(make_event("GET", f"{DOCS}/{doc.id}/file", token))
    assert resp["statusCode"] == 403, describe(resp)
    assert error_message(resp) == NOT_ASSIGNED

    resp = app.documents(make_event("GET", DOCS, token))
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp)["documents"] == []


def test_receiver_match_without_sub_gives_no_access(app):
    doc = seed_document(
        app.repo, app.storage, assigned_to=[sub_of(FAC_B)], receiver=name_of(FAC_A)
    )
    resp = app.documents(make_event("GET", f"{DOCS}/{doc.id}", token_of(FAC_A)))
    assert resp["statusCode"] == 403, describe(resp)
    assert error_message(resp) == NOT_ASSIGNED


def test_custom_role_claim_does_not_widen_list(app):
    add_doc(app, FAC_B)
    mine = add_doc(app, FAC_A)
    token = token_of(FAC_A, claims_overrides={"custom:role": "admin"})
    resp = app.documents(make_event("GET", DOCS, token))
    assert resp["statusCode"] == 200, describe(resp)
    assert [d["id"] for d in body_of(resp)["documents"]] == [mine.id]


# ---------------------------------------------------------------------------
# 404
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("who", [ADMIN, FAC_A])
def test_unknown_document_id_is_404_before_any_permission_check(app, who):
    token = token_of(who)
    expected = "Document was not found: nope"
    cases = [
        ("GET", f"{DOCS}/nope", None),
        ("GET", f"{DOCS}/nope/file", None),
        ("PATCH", f"{DOCS}/nope/status", {"status": "ACKNOWLEDGED"}),
        ("POST", f"{DOCS}/nope/assign", {"recipients": [sub_of(HEAD)]}),
    ]
    for method, path, body in cases:
        resp = app.documents(make_event(method, path, token, body=body))
        assert resp["statusCode"] == 404, describe(resp)
        assert error_message(resp) == expected


def test_unknown_route_is_404(app):
    resp = app.documents(make_event("GET", "/api/v1/nothing/here", token_of(ADMIN)))
    assert resp["statusCode"] == 404, describe(resp)
    assert error_message(resp) == "Route not found: GET /api/v1/nothing/here"


# ---------------------------------------------------------------------------
# 400: recipients and validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "recipients",
    ["head1@tu.ac.th", {"a": 1}, [1], [None], [sub_of(HEAD), 5], [["x"]]],
)
def test_recipients_must_be_a_list_of_strings(app, recipients):
    doc = add_doc(app, HEAD)
    resp = app.documents(
        make_event("POST", f"{DOCS}/{doc.id}/assign", token_of(ADMIN), body={"recipients": recipients})
    )
    assert resp["statusCode"] == 400, describe(resp)
    assert error_message(resp) == "recipients must be a list of strings"
    assert app.repo.get_by_id(doc.id).assigned_to == [sub_of(HEAD)]


def test_empty_recipients_list_is_400(app):
    doc = add_doc(app, HEAD)
    for body in ({"recipients": []}, {}):
        resp = app.documents(
            make_event("POST", f"{DOCS}/{doc.id}/assign", token_of(ADMIN), body=body)
        )
        assert resp["statusCode"] == 400, describe(resp)
        assert error_message(resp) == "Must assign at least one recipient user ID"


def test_blank_recipient_is_400(app):
    doc = add_doc(app, HEAD)
    resp = app.documents(
        make_event("POST", f"{DOCS}/{doc.id}/assign", token_of(ADMIN), body={"recipients": ["  "]})
    )
    assert resp["statusCode"] == 400, describe(resp)
    assert error_message(resp) == "Recipient is required"


def test_assign_unknown_recipient_is_400_and_changes_nothing(app):
    doc = add_doc(app, HEAD)
    resp = app.documents(
        make_event("POST", f"{DOCS}/{doc.id}/assign", token_of(ADMIN), body={"recipients": ["Nobody Here"]})
    )
    assert resp["statusCode"] == 400, describe(resp)
    assert error_message(resp) == "Unknown recipient: Nobody Here"
    stored = app.repo.get_by_id(doc.id)
    assert stored.assigned_to == [sub_of(HEAD)]
    assert len(stored.status_history) == len(doc.status_history)


def test_ambiguous_recipient_name_is_400(app):
    class TwinDirectory(FakeUserDirectory):
        def list_users(self):
            users = super().list_users()
            first = SEED_BY_EMAIL[FAC_A]
            clone = type(users[0])(
                sub="00000000-0000-4000-8000-0000000000ff",
                email="twin@tu.ac.th",
                name=first["name"],
            )
            return users + [clone]

    documents_handler.set_use_cases(
        build_document_use_cases(app.repo, app.storage, TwinDirectory())
    )
    resp = app.documents(register_event(token_of(ADMIN), name_of(FAC_A)))
    assert resp["statusCode"] == 400, describe(resp)
    assert error_message(resp) == f"Recipient name is ambiguous: {name_of(FAC_A)}"
    assert app.storage.stored_files() == []


def test_register_with_unknown_recipient_is_400_and_stores_no_file(app):
    resp = app.documents(register_event(token_of(ADMIN), "Ghost Person"))
    assert resp["statusCode"] == 400, describe(resp)
    assert error_message(resp) == "Unknown recipient: Ghost Person"
    assert app.storage.stored_files() == []
    assert app.repo._docs == {}


def test_register_with_blank_recipient_is_400_and_stores_no_file(app):
    resp = app.documents(register_event(token_of(ADMIN), "   "))
    assert resp["statusCode"] == 400, describe(resp)
    assert error_message(resp) == "Recipient is required"
    assert app.storage.stored_files() == []


@pytest.mark.parametrize("bad", ["{not json", "[1,", "nope"])
def test_bad_json_is_400(app, bad):
    doc = add_doc(app, HEAD)
    for method, suffix in (("POST", "assign"), ("PATCH", "status")):
        resp = app.documents(
            make_event(method, f"{DOCS}/{doc.id}/{suffix}", token_of(ADMIN), body=bad)
        )
        assert resp["statusCode"] == 400, describe(resp)
        assert error_message(resp)


def test_invalid_status_transition_is_400(app):
    doc = add_doc(app, HEAD)
    resp = app.documents(
        make_event("PATCH", f"{DOCS}/{doc.id}/status", token_of(HEAD), body={"status": "RECEIVED"})
    )
    assert resp["statusCode"] == 400, describe(resp)
    assert error_message(resp).startswith("Invalid workflow transition:")


def test_registration_validation_errors_are_400(app):
    token = token_of(ADMIN)

    resp = app.documents(register_event(token, name_of(HEAD), content_type="application/json"))
    assert resp["statusCode"] == 400, describe(resp)
    assert error_message(resp) == "Content-Type must be multipart/form-data"

    # An empty receiver value counts as a missing field.
    resp = app.documents(register_event(token, ""))
    assert resp["statusCode"] == 400, describe(resp)
    assert error_message(resp) == "Missing required fields: receiver"
    assert app.storage.stored_files() == []


# ---------------------------------------------------------------------------
# Success paths that depend on the verified caller
# ---------------------------------------------------------------------------

def test_admin_register_stores_sub_and_records_email_as_actor(app):
    resp = app.documents(register_event(token_of(ADMIN), name_of(HEAD)))
    assert resp["statusCode"] == 201, describe(resp)
    doc = body_of(resp)["document"]
    assert doc["receiver"] == name_of(HEAD)
    assert doc["status"] == "ASSIGNED"
    assert doc["assigned_to"] == [sub_of(HEAD)]
    assert doc["uploaded_by"] == ADMIN
    assert doc["status_history"][-1]["changed_by"] == ADMIN
    assert len(app.storage.stored_files()) == 1


def test_register_resolves_recipient_by_email(app):
    resp = app.documents(register_event(token_of(ADMIN), "HEAD1@tu.ac.th"))
    assert resp["statusCode"] == 201, describe(resp)
    doc = body_of(resp)["document"]
    assert doc["receiver"] == name_of(HEAD)
    assert doc["assigned_to"] == [sub_of(HEAD)]


def test_assigned_user_forwards_with_names_and_actor_is_email(app):
    doc = add_doc(app, HEAD)
    resp = app.documents(
        make_event(
            "POST",
            f"{DOCS}/{doc.id}/assign",
            token_of(HEAD),
            body={"recipients": [name_of(FAC_A)], "note": "please review"},
        )
    )
    assert resp["statusCode"] == 200, describe(resp)
    payload = body_of(resp)
    assert payload["status"] == "FORWARDED"
    assert payload["assigned_to"] == [sub_of(FAC_A)]

    stored = app.repo.get_by_id(doc.id)
    assert stored.assigned_to == [sub_of(FAC_A)]
    assert len(stored.status_history) == len(doc.status_history) + 1
    assert stored.status_history[-1].changed_by == HEAD

    # The old recipient lost access, the new one gained it.
    resp = app.documents(make_event("GET", f"{DOCS}/{doc.id}", token_of(HEAD)))
    assert resp["statusCode"] == 403, describe(resp)
    resp = app.documents(make_event("GET", f"{DOCS}/{doc.id}", token_of(FAC_A)))
    assert resp["statusCode"] == 200, describe(resp)


def test_status_change_by_assigned_user_records_email(app):
    doc = add_doc(app, HEAD)
    resp = app.documents(
        make_event("PATCH", f"{DOCS}/{doc.id}/status", token_of(HEAD), body={"status": "ACKNOWLEDGED"})
    )
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp)["status"] == "ACKNOWLEDGED"
    assert app.repo.get_by_id(doc.id).status_history[-1].changed_by == HEAD


def test_actor_falls_back_to_sub_when_token_has_no_email(app):
    doc = add_doc(app, HEAD)
    token = token_of(HEAD, claims_overrides={"email": None})
    resp = app.documents(
        make_event("PATCH", f"{DOCS}/{doc.id}/status", token, body={"status": "ACKNOWLEDGED"})
    )
    assert resp["statusCode"] == 200, describe(resp)
    assert app.repo.get_by_id(doc.id).status_history[-1].changed_by == sub_of(HEAD)


@pytest.mark.parametrize("via", ["bearer", "cookies", "cookie_header"])
def test_admin_lists_all_and_user_lists_only_assigned(app, via):
    mine = add_doc(app, HEAD)
    add_doc(app, FAC_A)

    resp = app.documents(make_event("GET", DOCS, token_of(ADMIN), via=via))
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp)["count"] == 2

    resp = app.documents(make_event("GET", DOCS, token_of(HEAD), via=via))
    assert resp["statusCode"] == 200, describe(resp)
    assert [d["id"] for d in body_of(resp)["documents"]] == [mine.id]


@pytest.mark.parametrize("download,prefix", [("true", "attachment"), (None, "inline")])
def test_file_response_keeps_disposition_and_base64_body(app, download, prefix):
    doc = add_doc(app, HEAD, file_name="letter.pdf", file_bytes=b"%PDF-1.4 hello")
    query = {"download": download} if download else None
    resp = app.documents(make_event("GET", f"{DOCS}/{doc.id}/file", token_of(HEAD), query=query))
    assert resp["statusCode"] == 200, describe(resp)
    assert resp["isBase64Encoded"] is True
    assert resp["headers"]["Content-Disposition"] == f'{prefix}; filename="letter.pdf"'
    assert base64.b64decode(resp["body"]) == b"%PDF-1.4 hello"


# ---------------------------------------------------------------------------
# OPTIONS stays first
# ---------------------------------------------------------------------------

def test_options_returns_204_without_token_and_without_touching_use_cases(monkeypatch):
    def boom():
        raise AssertionError("get_use_cases must not run for OPTIONS")

    monkeypatch.setattr(documents_handler, "get_use_cases", boom)
    resp = documents_handler.lambda_handler(make_event("OPTIONS", DOCS))
    assert resp["statusCode"] == 204
    assert resp["body"] == ""
    assert resp["headers"]["Access-Control-Allow-Methods"] == "GET,POST,PATCH,PUT,DELETE,OPTIONS"


def test_use_cases_are_built_inside_the_try_block(monkeypatch, caplog):
    def boom():
        raise RuntimeError("mongo is down: secret-host")

    monkeypatch.setattr(documents_handler, "get_use_cases", boom)
    resp = documents_handler.lambda_handler(make_event("GET", DOCS, token_of(ADMIN)))
    assert resp["statusCode"] == 500, describe(resp)
    assert body_of(resp) == {"error": {"message": INTERNAL}}
