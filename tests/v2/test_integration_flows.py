# Feature: backend-v2-auth-bypass-fix, integration flows
"""Offline integration flows across the auth, documents and search Lambdas.

Every flow uses the real CognitoTokenVerifier (static signing keys, installed by
the autouse fixture in conftest.py), the real use cases and the in-memory
adapters. Checks are made on HTTP results only.
"""
import base64

import pytest

from conftest import (
    SEED_BY_EMAIL,
    FakeCognitoClient,
    body_of,
    describe,
    error_message,
    make_event,
    make_multipart,
    make_user_token,
    seed_document,
)
from backend_v2.shared.domain import ALLOWED_STATUS_TRANSITIONS, DocumentStatus

DOCS = "/api/v1/documents"
TRIE = "/api/v1/search/trie"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"

ADMIN = "staff1@tu.ac.th"
HEAD = "head1@tu.ac.th"
FAC_A = "fac_a@tu.ac.th"
FAC_B = "fac_b@tu.ac.th"

NOT_ASSIGNED = "Access denied: You are not assigned to this document"
LOCATIONS = ["bearer", "cookies", "cookie_header"]


def name_of(email):
    return SEED_BY_EMAIL[email]["name"]


def sub_of(email):
    return SEED_BY_EMAIL[email]["sub"]


def login(auth_login, email, password="pw", token=None):
    """Log in through the auth Lambda; Cognito (faked) returns a signed token."""
    handler = auth_login(FakeCognitoClient(id_token=token or make_user_token(email)))
    resp = handler(make_event("POST", LOGIN, body={"email": email, "password": password}))
    assert resp["statusCode"] == 200, describe(resp)
    return resp


def cookie_value(set_cookie):
    """Token inside an `auth_token=<value>; Path=/; ...` cookie string."""
    first = set_cookie.split(";", 1)[0].strip()
    assert first.startswith("auth_token="), set_cookie
    return first[len("auth_token="):]


def doc_ids(resp):
    assert resp["statusCode"] == 200, describe(resp)
    return {d["id"] for d in body_of(resp)["documents"]}


def trie_count(resp):
    assert resp["statusCode"] == 200, describe(resp)
    return body_of(resp)["count"]


# ---------------------------------------------------------------------------
# Flow 1: full routing flow
# ---------------------------------------------------------------------------

def test_full_routing_flow_from_registration_to_forwarding(app, auth_login):
    # HEAD-1 forwards from ASSIGNED, so FAC-B acknowledges from FORWARDED.
    # The acknowledge status comes from the state machine, not from a guess.
    assert DocumentStatus.FORWARDED in ALLOWED_STATUS_TRANSITIONS[DocumentStatus.ASSIGNED]
    after_forward = ALLOWED_STATUS_TRANSITIONS[DocumentStatus.FORWARDED]
    acknowledged = sorted(after_forward, key=lambda s: s.value)[0]
    assert acknowledged in after_forward

    admin_token = body_of(login(auth_login, ADMIN))["token"]
    head_login = login(auth_login, HEAD)
    assert body_of(head_login)["user"]["role"] == "user"
    head_token = body_of(head_login)["token"]

    # Admin registers the document to HEAD-1 by display name.
    fields = {
        "title": "Budget letter",
        "document_date": "2025-02-01",
        "sender": "Finance Office",
        "receiver": name_of(HEAD),
    }
    content_type, multipart_body = make_multipart(fields, file_text="%PDF-1.4 routed letter")
    resp = app.documents(
        make_event(
            "POST",
            DOCS,
            admin_token,
            body=multipart_body,
            headers={"content-type": content_type},
        )
    )
    assert resp["statusCode"] == 201, describe(resp)
    created = body_of(resp)["document"]
    doc_id = created["id"]
    assert created["status"] == DocumentStatus.ASSIGNED.value
    assert created["assigned_to"] == [sub_of(HEAD)]

    # HEAD-1 lists the document and sees exactly that one.
    assert doc_ids(app.documents(make_event("GET", DOCS, head_token))) == {doc_id}

    # HEAD-1 forwards the document to FAC-B by display name (ASSIGNED -> FORWARDED).
    resp = app.documents(
        make_event(
            "POST",
            f"{DOCS}/{doc_id}/assign",
            head_token,
            body={"recipients": [name_of(FAC_B)]},
        )
    )
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp)["status"] == DocumentStatus.FORWARDED.value
    assert body_of(resp)["assigned_to"] == [sub_of(FAC_B)]

    # FAC-B lists the document, acknowledges it (FORWARDED -> ACKNOWLEDGED) and downloads it.
    fac_b_token = body_of(login(auth_login, FAC_B))["token"]
    assert doc_ids(app.documents(make_event("GET", DOCS, fac_b_token))) == {doc_id}
    resp = app.documents(
        make_event("PATCH", f"{DOCS}/{doc_id}/status", fac_b_token, body={"status": acknowledged.value})
    )
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp)["status"] == acknowledged.value
    resp = app.documents(
        make_event("GET", f"{DOCS}/{doc_id}/file", fac_b_token, query={"download": "true"})
    )
    assert resp["statusCode"] == 200, describe(resp)
    assert resp["isBase64Encoded"] is True
    assert resp["headers"]["Content-Disposition"].startswith("attachment")
    assert base64.b64decode(resp["body"]) == b"%PDF-1.4 routed letter"

    # HEAD-1 no longer has access.
    for path in (f"{DOCS}/{doc_id}", f"{DOCS}/{doc_id}/file"):
        resp = app.documents(make_event("GET", path, head_token))
        assert resp["statusCode"] == 403, describe(resp)
        assert error_message(resp) == NOT_ASSIGNED
    assert doc_ids(app.documents(make_event("GET", DOCS, head_token))) == set()
    assert trie_count(app.search(make_event("GET", TRIE, head_token))) == 0
    resp = app.documents(
        make_event("PATCH", f"{DOCS}/{doc_id}/status", head_token, body={"status": acknowledged.value})
    )
    assert resp["statusCode"] == 403, describe(resp)
    assert error_message(resp) == "Access denied: You cannot update an unassigned document"


# ---------------------------------------------------------------------------
# Flow 2: token transport
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("via", LOCATIONS)
def test_same_token_works_through_every_location_in_documents_and_search(app, via):
    token = make_user_token(HEAD)
    own = seed_document(
        app.repo, app.storage, assigned_to=[sub_of(HEAD)], receiver=name_of(HEAD), title="mine"
    )
    seed_document(
        app.repo, app.storage, assigned_to=[sub_of(FAC_A)], receiver=name_of(FAC_A), title="other"
    )

    assert doc_ids(app.documents(make_event("GET", DOCS, token, via=via))) == {own.id}
    assert trie_count(app.search(make_event("GET", TRIE, token, via=via))) == 1


def test_cookie_value_from_login_response_authenticates_next_requests(app, auth_login):
    own = seed_document(
        app.repo, app.storage, assigned_to=[sub_of(HEAD)], receiver=name_of(HEAD), title="mine"
    )
    seed_document(
        app.repo, app.storage, assigned_to=[sub_of(FAC_A)], receiver=name_of(FAC_A), title="other"
    )

    resp = login(auth_login, HEAD)
    body_token = body_of(resp)["token"]
    cookie = resp["cookies"][0]
    assert cookie == resp["headers"]["Set-Cookie"]
    assert cookie.startswith("auth_token=")
    cookie_token = cookie_value(cookie)
    assert cookie_token == body_token

    # The cookie value works in the cookies array and in the Cookie header.
    for via in ("cookies", "cookie_header"):
        assert doc_ids(app.documents(make_event("GET", DOCS, cookie_token, via=via))) == {own.id}
        assert trie_count(app.search(make_event("GET", TRIE, cookie_token, via=via))) == 1

    # The token from the response body works as a Bearer header.
    assert doc_ids(app.documents(make_event("GET", DOCS, body_token, via="bearer"))) == {own.id}
    assert trie_count(app.search(make_event("GET", TRIE, body_token, via="bearer"))) == 1

    # The auth Lambda accepts the same cookie value as well.
    me = auth_login(FakeCognitoClient(id_token=body_token))
    resp = me(make_event("GET", ME, cookie_token, via="cookies"))
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp)["user"]["sub"] == sub_of(HEAD)


# ---------------------------------------------------------------------------
# Flow 3: cross-Lambda admin consistency
# ---------------------------------------------------------------------------

def admin_decisions(app, auth_login, token):
    """Admin decision of each Lambda, read from HTTP results only.

    documents: an unassigned document is listed. search: the same document is
    in the trie. auth: the login role and the groups returned by /auth/me.
    """
    app.repo._docs.clear()
    app.repo._counter = 0
    unassigned = seed_document(
        app.repo, app.storage, assigned_to=[], receiver="Nobody", title="Unassigned"
    )

    in_documents = unassigned.id in doc_ids(app.documents(make_event("GET", DOCS, token)))
    in_search = trie_count(app.search(make_event("GET", TRIE, token))) == 1

    handler = auth_login(FakeCognitoClient(id_token=token))
    login_resp = handler(make_event("POST", LOGIN, body={"email": "caller@example.com", "password": "pw"}))
    assert login_resp["statusCode"] == 200, describe(login_resp)
    login_admin = body_of(login_resp)["user"]["role"] == "admin"

    me_resp = handler(make_event("GET", ME, token))
    assert me_resp["statusCode"] == 200, describe(me_resp)
    me_admin = "ADMIN" in (body_of(me_resp)["user"].get("cognito:groups") or [])

    return in_documents, in_search, login_admin, me_admin


@pytest.mark.parametrize("email", ["staff1@tu.ac.th", "admin1@tu.ac.th", "admin2@tu.ac.th"])
def test_admin_token_is_admin_in_documents_search_and_auth(app, auth_login, email):
    token = make_user_token(email)

    assert admin_decisions(app, auth_login, token) == (True, True, True, True)


@pytest.mark.parametrize(
    "overrides",
    [
        {"email": "admin@kanna.ac.th"},
        {"name": "Admin Staff"},
        {"email": "admin@kanna.ac.th", "name": "Admin Staff", "custom:role": "admin", "role": "admin"},
        {"cognito:groups": ["admin", "STAFF"], "custom:role": "admin"},
    ],
    ids=["admin-email", "admin-name", "admin-looking-claims", "lowercase-admin-group"],
)
def test_non_admin_with_admin_looking_values_is_not_admin_anywhere(app, auth_login, overrides):
    token = make_user_token(HEAD, overrides)

    assert admin_decisions(app, auth_login, token) == (False, False, False, False)
