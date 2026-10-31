"""Search handler: authentication, authorization and error mapping.

Covers the search rows of the HTTP status and message table in design.md.
All tests run offline with the fakes from conftest.py.
"""
import logging

import pytest

from conftest import (
    SEED_BY_EMAIL,
    SpyUseCases,
    body_of,
    describe,
    error_message,
    make_event,
    make_none_token,
    make_user_token,
    seed_document,
)
from backend_v2.functions.search import handler as search_handler

TRIE = "/api/v1/search/trie"

ADMIN = "staff1@tu.ac.th"
HEAD = "head1@tu.ac.th"
FAC_A = "fac_a@tu.ac.th"
FAC_B = "fac_b@tu.ac.th"

AUTH_REQUIRED = "Authentication is required"
INVALID_TOKEN = "Invalid authentication token"
INTERNAL = "Internal server error"


def sub_of(email):
    return SEED_BY_EMAIL[email]["sub"]


def name_of(email):
    return SEED_BY_EMAIL[email]["name"]


def add_doc(app, owner_email, **kwargs):
    return seed_document(
        app.repo,
        app.storage,
        assigned_to=[sub_of(owner_email)],
        receiver=name_of(owner_email),
        **kwargs,
    )


def trie_doc_ids(trie):
    """Collect every document id stored under the "$" keys of a serialized trie."""
    ids = set()
    stack = [trie]
    while stack:
        node = stack.pop()
        for key, value in node.items():
            if key == "$":
                ids.update(value)
            else:
                stack.append(value)
    return ids


def test_options_returns_204_without_authentication():
    resp = search_handler.lambda_handler(make_event("OPTIONS", TRIE))
    assert resp["statusCode"] == 204
    assert resp["body"] == ""


def test_no_token_is_401_and_no_use_case_runs():
    spy = SpyUseCases()
    search_handler.set_use_cases(spy)
    try:
        resp = search_handler.lambda_handler(make_event("GET", TRIE))
    finally:
        search_handler.set_use_cases(None)
    assert resp["statusCode"] == 401, describe(resp)
    assert error_message(resp) == AUTH_REQUIRED
    assert spy.calls == []


@pytest.mark.parametrize("via", ["bearer", "cookies", "cookie_header"])
def test_invalid_token_is_401_and_no_use_case_runs(via):
    token = make_none_token({"sub": "attacker", "cognito:groups": ["ADMIN"]})
    spy = SpyUseCases()
    search_handler.set_use_cases(spy)
    try:
        resp = search_handler.lambda_handler(make_event("GET", TRIE, token, via=via))
    finally:
        search_handler.set_use_cases(None)
    assert resp["statusCode"] == 401, describe(resp)
    assert error_message(resp) == INVALID_TOKEN
    assert spy.calls == []


def test_admin_sees_all_documents_in_trie(app):
    d1 = add_doc(app, HEAD, title="alpha budget")
    d2 = add_doc(app, FAC_A, title="beta report")
    d3 = add_doc(app, FAC_B, title="gamma memo")

    resp = app.search(make_event("GET", TRIE, make_user_token(ADMIN)))

    assert resp["statusCode"] == 200, describe(resp)
    body = body_of(resp)
    assert body["count"] == 3
    assert trie_doc_ids(body["trie"]) == {d1.id, d2.id, d3.id}


def test_user_sees_only_documents_with_their_sub(app):
    own = add_doc(app, FAC_A, title="alpha budget")
    add_doc(app, FAC_B, title="beta report")
    add_doc(app, HEAD, title="gamma memo")

    resp = app.search(make_event("GET", TRIE, make_user_token(FAC_A)))

    assert resp["statusCode"] == 200, describe(resp)
    body = body_of(resp)
    assert body["count"] == 1
    assert trie_doc_ids(body["trie"]) == {own.id}


def test_user_is_not_matched_by_display_name(app):
    # Document names FAC-A as receiver but is assigned to another sub.
    seed_document(
        app.repo,
        app.storage,
        assigned_to=[sub_of(FAC_B)],
        receiver=name_of(FAC_A),
    )

    resp = app.search(make_event("GET", TRIE, make_user_token(FAC_A)))

    assert resp["statusCode"] == 200, describe(resp)
    body = body_of(resp)
    assert body["count"] == 0
    assert trie_doc_ids(body["trie"]) == set()


def test_unknown_route_is_404_for_authenticated_caller(app):
    resp = app.search(make_event("GET", "/api/v1/search/other", make_user_token(ADMIN)))
    assert resp["statusCode"] == 404, describe(resp)


def test_unhandled_error_is_generic_500_and_logged(app, caplog):
    app.repo.raise_on_find_all = RuntimeError("secret-host:27017")
    with caplog.at_level(logging.DEBUG):
        resp = app.search(make_event("GET", TRIE, make_user_token(ADMIN)))

    assert resp["statusCode"] == 500, describe(resp)
    assert body_of(resp) == {"error": {"message": INTERNAL}}
    assert "secret-host" not in resp["body"]
    assert any("Unhandled error in search handler" in r.getMessage() for r in caplog.records)
