# Feature: backend-v2-auth-bypass-fix, Property 2
"""Preservation: legitimate requests behave as before.

Every expectation below was observed on the unfixed code (commit b52f483) and
is asserted as a fixed value, so the same file must keep passing after the fix.
Inputs are outside the bug condition: tokens are signed by the test key with the
right issuer, audience and token_use and a future expiry. Documents hold only
lowercase sub values in assigned_to. Non-admin users carry no custom:role or
role claim, and their email and name never contain "admin" or "staff".
Admin users come from the seed users in group ADMIN.
"""
import json
import string

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from conftest import (
    SEED_BY_EMAIL,
    SEED_USERS,
    FakeCognitoClient,
    body_of,
    describe,
    error_message,
    make_event,
    make_multipart,
    make_token,
    make_user_token,
    seed_document,
)
from backend_v2.shared.domain.document_status import (
    ALLOWED_STATUS_TRANSITIONS,
    DocumentStatus,
)

PBT = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# ---------------------------------------------------------------------------
# Observed values (unfixed code)
# ---------------------------------------------------------------------------

DOCUMENTS_CORS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,Cookie",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,PUT,DELETE,OPTIONS",
}
SEARCH_CORS = {**DOCUMENTS_CORS, "Access-Control-Allow-Methods": "GET,OPTIONS"}
AUTH_CORS = {**DOCUMENTS_CORS, "Access-Control-Allow-Methods": "GET,POST,OPTIONS"}

LOGIN_COOKIE_SUFFIX = "; Path=/; HttpOnly; Secure; SameSite=None; Max-Age=86400"
CLEAR_COOKIE = "auth_token=; Path=/; HttpOnly; Secure; SameSite=None; Max-Age=0"

NOT_ASSIGNED = "Access denied: You are not assigned to this document"
CANNOT_UPDATE = "Access denied: You cannot update an unassigned document"
CANNOT_ASSIGN = "Permission required to assign or forward this document"
AUTH_REQUIRED = "Authentication is required"

DOCS = "/api/v1/documents"
TRIE = "/api/v1/search/trie"

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

ADMIN_USERS = [u for u in SEED_USERS if u["group"] == "ADMIN"]
SEED_NON_ADMINS = [u for u in SEED_USERS if u["group"] != "ADMIN"]
assert {u["email"] for u in ADMIN_USERS} == {
    "staff1@tu.ac.th",
    "admin1@tu.ac.th",
    "admin2@tu.ac.th",
}

_NAME_ALPHABET = string.ascii_lowercase + "กขคงจฉชซญดตทนบปผพมยรลวสหอฮ"


def _clean(value: str) -> bool:
    folded = value.casefold()
    return "admin" not in folded and "staff" not in folded


tokens_via = st.sampled_from(["bearer", "cookies", "cookie_header"])
admin_users = st.sampled_from(ADMIN_USERS)
seed_users = st.sampled_from(SEED_USERS)


@st.composite
def generated_users(draw):
    """Non-admin user with a random sub, email and display name.

    The fixed prefixes keep the values away from the seed emails, names and
    legacy alias keys. The text is filtered so it never contains admin or staff.
    """
    sub = str(draw(st.uuids())).lower()
    local = draw(st.text(alphabet=string.ascii_lowercase + string.digits, min_size=3, max_size=10))
    text = draw(st.text(alphabet=_NAME_ALPHABET, min_size=1, max_size=12))
    email = f"gen.{local}@tu.ac.th"
    name = f"gen {text}"
    if not (_clean(email) and _clean(name)):
        # Never draw admin-looking values; fall back to a safe constant.
        email, name = f"gen.{sub[:8]}@tu.ac.th", f"gen {sub[:8]}"
    return {
        "sub": sub,
        "email": email,
        "name": name,
        "group": "USER",
        "faculty": "คณะทดสอบ",
    }


non_admin_users = st.one_of(st.sampled_from(SEED_NON_ADMINS), generated_users())

text_field = st.text(
    alphabet=string.ascii_letters + string.digits + " กขคงจฉชซญดตทนบปผพมยรลวสหอฮ",
    min_size=1,
    max_size=30,
).filter(lambda s: s.strip() == s and s != "")

file_names = st.sampled_from(["a.pdf", "สำคัญ.pdf", "report 2025.pdf"])


def _identity_keys(user):
    return (user["sub"], user["email"].casefold(), user["name"].casefold())


@st.composite
def non_admin_with_others(draw, max_others=3):
    caller = draw(non_admin_users)
    others = draw(st.lists(non_admin_users, min_size=0, max_size=max_others))
    # Users in one scenario must differ in sub, email and display name. A shared
    # name is itself the name-collision case, which is outside the preserved inputs.
    taken = set(_identity_keys(caller))
    distinct = []
    for user in others:
        keys = set(_identity_keys(user))
        if not keys & taken:
            taken |= keys
            distinct.append(user)
    return caller, distinct


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def token_for(user, **kwargs):
    return make_token(
        {
            "sub": user["sub"],
            "email": user["email"],
            "name": user["name"],
            "cognito:groups": [user["group"]],
            "custom:faculty": user["faculty"],
        },
        **kwargs,
    )


def reset(app):
    app.repo._docs.clear()
    app.repo._counter = 0


def add_docs(app, owners, file_name="a.pdf"):
    """One document per owner: assigned_to=[owner sub], receiver=owner name."""
    return [
        seed_document(
            app.repo,
            app.storage,
            assigned_to=[owner["sub"]],
            receiver=owner["name"],
            title=f"Document {index}",
            file_name=file_name,
            file_bytes=f"%PDF-1.4 body {index}".encode(),
        )
        for index, owner in enumerate(owners)
    ]


def trie_ids(node):
    found = set()
    if isinstance(node, dict):
        for key, child in node.items():
            if key == "$":
                found.update(child)
            else:
                found.update(trie_ids(child))
    return found


def doc_ids(resp):
    return {d["id"] for d in body_of(resp)["documents"]}


def headers_of(resp):
    return resp.get("headers") or {}


# ---------------------------------------------------------------------------
# 1. Admin access (Req 3.5, 3.7, 3.14)
# ---------------------------------------------------------------------------

@PBT
@given(
    admin=admin_users,
    via=tokens_via,
    owners=st.lists(non_admin_users, min_size=2, max_size=4),
    file_name=file_names,
    target=st.sampled_from([DocumentStatus.ACKNOWLEDGED, DocumentStatus.FORWARDED]),
)
def test_admin_has_full_access(app, admin, via, owners, file_name, target):
    reset(app)
    docs = add_docs(app, owners, file_name=file_name)
    token = token_for(admin)

    resp = app.documents(make_event("GET", DOCS, token, via=via))
    assert resp["statusCode"] == 200, describe(resp)
    assert doc_ids(resp) == {d.id for d in docs}
    assert body_of(resp)["count"] == len(docs)

    resp = app.search(make_event("GET", TRIE, token, via=via))
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp)["count"] == len(docs)
    assert trie_ids(body_of(resp)["trie"]) == {d.id for d in docs}

    for doc in docs:
        resp = app.documents(make_event("GET", f"{DOCS}/{doc.id}", token, via=via))
        assert resp["statusCode"] == 200, describe(resp)
        assert body_of(resp)["id"] == doc.id

        resp = app.documents(make_event("GET", f"{DOCS}/{doc.id}/file", token, via=via))
        assert resp["statusCode"] == 200, describe(resp)
        assert resp["isBase64Encoded"] is True
        assert headers_of(resp)["Content-Disposition"] == f'inline; filename="{file_name}"'

        resp = app.documents(
            make_event("GET", f"{DOCS}/{doc.id}/file", token, via=via, query={"download": "true"})
        )
        assert resp["statusCode"] == 200, describe(resp)
        assert resp["isBase64Encoded"] is True
        assert headers_of(resp)["Content-Disposition"] == f'attachment; filename="{file_name}"'
        assert headers_of(resp)["Content-Type"] == "application/pdf"

    first, last = docs[0], docs[-1]
    resp = app.documents(
        make_event(
            "PATCH", f"{DOCS}/{first.id}/status", token, via=via, body={"status": target.value}
        )
    )
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp) == {"message": "Status updated successfully", "status": target.value}

    recipient = SEED_BY_EMAIL["user3@tu.ac.th"]
    resp = app.documents(
        make_event(
            "POST",
            f"{DOCS}/{last.id}/assign",
            token,
            via=via,
            body={"recipients": [recipient["sub"]], "note": "n"},
        )
    )
    assert resp["statusCode"] == 200, describe(resp)
    body = body_of(resp)
    assert body["message"] == "Document assigned successfully"
    assert body["status"] == "FORWARDED"


# ---------------------------------------------------------------------------
# 2. Assigned recipient (Req 3.5, 3.9, 3.14)
# ---------------------------------------------------------------------------

@PBT
@given(
    data=non_admin_with_others(),
    via=tokens_via,
    mine_count=st.integers(min_value=1, max_value=3),
    target=st.sampled_from(list(DocumentStatus)),
    file_name=file_names,
)
def test_assigned_user_sees_and_works_only_on_assigned_documents(
    app, data, via, mine_count, target, file_name
):
    caller, others = data
    reset(app)
    mine = add_docs(app, [caller] * mine_count, file_name=file_name)
    theirs = add_docs(app, others)
    token = token_for(caller)

    resp = app.documents(make_event("GET", DOCS, token, via=via))
    assert resp["statusCode"] == 200, describe(resp)
    assert doc_ids(resp) == {d.id for d in mine}
    assert body_of(resp)["count"] == len(mine)

    resp = app.search(make_event("GET", TRIE, token, via=via))
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp)["count"] == len(mine)
    assert trie_ids(body_of(resp)["trie"]) == {d.id for d in mine}
    assert not trie_ids(body_of(resp)["trie"]) & {d.id for d in theirs}

    for doc in mine:
        resp = app.documents(make_event("GET", f"{DOCS}/{doc.id}", token, via=via))
        assert resp["statusCode"] == 200, describe(resp)
        resp = app.documents(make_event("GET", f"{DOCS}/{doc.id}/file", token, via=via))
        assert resp["statusCode"] == 200, describe(resp)
        assert headers_of(resp)["Content-Disposition"] == f'inline; filename="{file_name}"'
        resp = app.documents(
            make_event("GET", f"{DOCS}/{doc.id}/file", token, via=via, query={"download": "true"})
        )
        assert resp["statusCode"] == 200, describe(resp)
        assert headers_of(resp)["Content-Disposition"] == f'attachment; filename="{file_name}"'

    # Documents start as ASSIGNED. Allowed targets give 200, every other status gives 400.
    resp = app.documents(
        make_event(
            "PATCH",
            f"{DOCS}/{mine[0].id}/status",
            token,
            via=via,
            body={"status": target.value, "note": "n"},
        )
    )
    if target in ALLOWED_STATUS_TRANSITIONS[DocumentStatus.ASSIGNED]:
        assert resp["statusCode"] == 200, describe(resp)
        assert body_of(resp) == {"message": "Status updated successfully", "status": target.value}
    else:
        assert resp["statusCode"] == 400, describe(resp)
        assert error_message(resp).startswith("Invalid workflow transition:"), describe(resp)


# ---------------------------------------------------------------------------
# 3. Non-assigned user (Req 3.10)
# ---------------------------------------------------------------------------

@PBT
@given(
    data=non_admin_with_others(max_others=3),
    via=tokens_via,
)
def test_non_assigned_user_is_denied_and_does_not_see_the_documents(app, data, via):
    caller, others = data
    if not others:
        others = [SEED_NON_ADMINS[0] if SEED_NON_ADMINS[0]["sub"] != caller["sub"] else SEED_NON_ADMINS[1]]
    reset(app)
    docs = add_docs(app, others)
    token = token_for(caller)

    for doc in docs:
        resp = app.documents(make_event("GET", f"{DOCS}/{doc.id}", token, via=via))
        assert resp["statusCode"] == 403, describe(resp)
        assert error_message(resp) == NOT_ASSIGNED

        resp = app.documents(make_event("GET", f"{DOCS}/{doc.id}/file", token, via=via))
        assert resp["statusCode"] == 403, describe(resp)
        assert error_message(resp) == NOT_ASSIGNED

        resp = app.documents(
            make_event(
                "PATCH", f"{DOCS}/{doc.id}/status", token, via=via, body={"status": "ACKNOWLEDGED"}
            )
        )
        assert resp["statusCode"] == 403, describe(resp)
        assert error_message(resp) == CANNOT_UPDATE

        resp = app.documents(
            make_event(
                "POST",
                f"{DOCS}/{doc.id}/assign",
                token,
                via=via,
                body={"recipients": [SEED_BY_EMAIL["user3@tu.ac.th"]["sub"]]},
            )
        )
        assert resp["statusCode"] == 403, describe(resp)
        assert error_message(resp) == CANNOT_ASSIGN

    resp = app.documents(make_event("GET", DOCS, token, via=via))
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp) == {"documents": [], "count": 0}

    resp = app.search(make_event("GET", TRIE, token, via=via))
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp) == {"trie": {}, "count": 0}

    # Nothing changed by the denied calls.
    for doc in docs:
        stored = app.repo.get_by_id(doc.id)
        assert stored.status == DocumentStatus.ASSIGNED
        assert stored.assigned_to == doc.assigned_to
        assert len(stored.status_history) == 1


# ---------------------------------------------------------------------------
# 4. Assign and forward semantics (Req 3.11)
# ---------------------------------------------------------------------------

@PBT
@given(
    admin=admin_users,
    first=st.sampled_from(SEED_NON_ADMINS),
    second=seed_users,
    via=tokens_via,
    note=st.text(alphabet=string.ascii_letters + " ", max_size=20).filter(lambda s: s == s.strip()),
)
def test_assign_then_forward_replaces_assigned_to_and_appends_one_history_entry(
    app, admin, first, second, via, note
):
    reset(app)
    doc = seed_document(
        app.repo, app.storage, assigned_to=[], receiver="Registrar Desk", title="Unassigned"
    )
    assert app.repo.get_by_id(doc.id).status == DocumentStatus.RECEIVED
    admin_token = token_for(admin)

    # RECEIVED -> ASSIGNED, by an admin.
    resp = app.documents(
        make_event(
            "POST",
            f"{DOCS}/{doc.id}/assign",
            admin_token,
            via=via,
            body={"recipients": [first["sub"]], "note": note},
        )
    )
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp)["status"] == "ASSIGNED"
    stored = app.repo.get_by_id(doc.id)
    assert stored.assigned_to == [first["sub"]]
    assert stored.status == DocumentStatus.ASSIGNED
    assert len(stored.status_history) == 2
    assert stored.status_history[-1].status == DocumentStatus.ASSIGNED
    assert stored.status_history[-1].changed_by == admin["email"]
    assert stored.status_history[-1].note == note

    # ASSIGNED -> FORWARDED, by the assigned non-admin recipient.
    resp = app.documents(
        make_event(
            "POST",
            f"{DOCS}/{doc.id}/assign",
            token_for(first),
            via=via,
            body={"recipients": [second["sub"]]},
        )
    )
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp)["status"] == "FORWARDED"
    assert body_of(resp)["message"] == "Document assigned successfully"
    stored = app.repo.get_by_id(doc.id)
    assert stored.assigned_to == [second["sub"]]
    assert stored.status == DocumentStatus.FORWARDED
    assert len(stored.status_history) == 3
    assert stored.status_history[-1].status == DocumentStatus.FORWARDED
    assert stored.status_history[-1].changed_by == first["email"]


# ---------------------------------------------------------------------------
# 5. Login and logout (Req 3.1, 3.2, 3.3, 3.4)
# ---------------------------------------------------------------------------

@PBT
@given(user=seed_users, email_case=st.sampled_from(["same", "upper", "padded"]))
def test_login_returns_user_token_and_cookie(auth_login, user, email_case):
    token = token_for(user)
    client = FakeCognitoClient(id_token=token)
    handler = auth_login(client)
    typed = {
        "same": user["email"],
        "upper": user["email"].upper(),
        "padded": f"  {user['email']}  ",
    }[email_case]

    resp = handler(make_event("POST", "/api/v1/auth/login", body={"email": typed, "password": "pw"}))

    assert resp["statusCode"] == 200, describe(resp)
    body = body_of(resp)
    assert set(body) == {"user", "token"}
    assert body["token"] == token
    assert set(body["user"]) == {"id", "email", "name", "role", "faculty"}
    assert body["user"] == {
        "id": user["sub"],
        "email": user["email"],
        "name": user["name"],
        "role": "admin" if user["group"] == "ADMIN" else "user",
        "faculty": user["faculty"],
    }
    cookie = f"auth_token={token}{LOGIN_COOKIE_SUFFIX}"
    assert resp["headers"]["Set-Cookie"] == cookie
    assert resp["cookies"] == [cookie]
    assert client.calls[0]["AuthParameters"] == {"USERNAME": user["email"], "PASSWORD": "pw"}


@pytest.mark.parametrize("code", ["NotAuthorizedException", "UserNotFoundException"])
def test_login_with_bad_credentials_is_401(auth_login, code):
    handler = auth_login(FakeCognitoClient(error=FakeCognitoClient.client_error(code)))
    resp = handler(
        make_event("POST", "/api/v1/auth/login", body={"email": "head1@tu.ac.th", "password": "x"})
    )
    assert resp["statusCode"] == 401, describe(resp)
    assert body_of(resp) == {"error": {"message": "Invalid email or password"}}
    assert "Set-Cookie" not in resp["headers"]
    assert "cookies" not in resp


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "", "password": "pw"},
        {"email": "   ", "password": "pw"},
        {"email": "head1@tu.ac.th", "password": ""},
        {"email": "", "password": ""},
        {"password": "pw"},
        {"email": "head1@tu.ac.th"},
        {},
    ],
)
def test_login_with_empty_email_or_password_is_400(auth_login, payload):
    client = FakeCognitoClient(id_token=token_for(SEED_BY_EMAIL["head1@tu.ac.th"]))
    handler = auth_login(client)
    resp = handler(make_event("POST", "/api/v1/auth/login", body=payload))
    assert resp["statusCode"] == 400, describe(resp)
    assert body_of(resp) == {"error": {"message": "Email and password are required"}}
    assert client.calls == []


@PBT
@given(via=st.sampled_from([None, "bearer", "cookies", "cookie_header"]))
def test_logout_succeeds_without_a_token_and_clears_the_cookie(auth_login, via):
    handler = auth_login(FakeCognitoClient())
    token = None if via is None else token_for(SEED_BY_EMAIL["head1@tu.ac.th"])
    resp = handler(make_event("POST", "/api/v1/auth/logout", token, via=via or "bearer"))
    assert resp["statusCode"] == 200, describe(resp)
    assert body_of(resp) == {"message": "Logged out successfully"}
    assert resp["headers"]["Set-Cookie"] == CLEAR_COOKIE
    assert resp["cookies"] == [CLEAR_COOKIE]


# ---------------------------------------------------------------------------
# 6. Registration by an admin (Req 3.7, 3.8)
# ---------------------------------------------------------------------------

@PBT
@given(
    admin=admin_users,
    recipient=seed_users,
    title=text_field,
    sender=text_field,
    via=tokens_via,
)
def test_admin_registers_document_addressed_to_a_display_name(
    app, admin, recipient, title, sender, via
):
    reset(app)
    content_type, body = make_multipart(
        {
            "title": title,
            "document_date": "2025-01-15",
            "sender": sender,
            "receiver": recipient["name"],
        }
    )
    event = make_event(
        "POST",
        DOCS,
        token_for(admin),
        via=via,
        body=body,
        headers={"content-type": content_type},
    )
    resp = app.documents(event)

    assert resp["statusCode"] == 201, describe(resp)
    payload = body_of(resp)
    assert payload["message"] == "Document registered successfully"
    doc = payload["document"]
    assert doc["title"] == title
    assert doc["sender"] == sender
    assert doc["receiver"] == recipient["name"]
    assert doc["status"] == "ASSIGNED"
    assert doc["uploaded_by"] == admin["email"]
    assert doc["file_name"] == "letter.pdf"
    assert len(doc["status_history"]) == 2
    assert [e["status"] for e in doc["status_history"]] == ["RECEIVED", "ASSIGNED"]
    assert doc["status_history"][-1]["changed_by"] == admin["email"]
    # assigned_to is not compared: names before the fix, sub values after it.
    assert len(app.repo._docs) == 1


@PBT
@given(user=non_admin_users, via=tokens_via)
def test_non_admin_cannot_register_documents(app, user, via):
    reset(app)
    content_type, body = make_multipart(
        {"title": "T", "document_date": "2025-01-15", "sender": "S", "receiver": "R"}
    )
    event = make_event(
        "POST", DOCS, token_for(user), via=via, body=body, headers={"content-type": content_type}
    )
    resp = app.documents(event)
    assert resp["statusCode"] == 403, describe(resp)
    assert error_message(resp) == "Admin privilege required to register documents"
    assert app.repo._docs == {}


# ---------------------------------------------------------------------------
# 7. Request validation, preflight and missing token (Req 3.6, 3.12, 3.13, 3.15)
# ---------------------------------------------------------------------------

@PBT
@given(
    user=st.one_of(st.sampled_from(ADMIN_USERS), non_admin_users),
    via=tokens_via,
    missing=st.text(alphabet=string.ascii_lowercase + string.digits + "-", min_size=1, max_size=20),
)
def test_unknown_document_id_is_404_before_any_permission_check(app, user, via, missing):
    reset(app)
    missing_id = f"none-{missing}"
    token = token_for(user)
    expected = f"Document was not found: {missing_id}"
    calls = [
        ("GET", f"{DOCS}/{missing_id}", None),
        ("GET", f"{DOCS}/{missing_id}/file", None),
        ("PATCH", f"{DOCS}/{missing_id}/status", {"status": "ACKNOWLEDGED"}),
        ("POST", f"{DOCS}/{missing_id}/assign", {"recipients": [SEED_USERS[0]["sub"]]}),
    ]
    for method, path, body in calls:
        resp = app.documents(make_event(method, path, token, via=via, body=body))
        assert resp["statusCode"] == 404, describe(resp)
        assert error_message(resp) == expected, describe(resp)


@PBT
@given(
    admin=admin_users,
    via=tokens_via,
    bad=st.sampled_from(["{not json", "[1,", "}", "nope"]),
)
def test_bad_json_body_is_400_with_the_parser_message(app, admin, via, bad):
    reset(app)
    doc = add_docs(app, [SEED_BY_EMAIL["head1@tu.ac.th"]])[0]
    try:
        json.loads(bad)
    except ValueError as err:
        expected = str(err)
    token = token_for(admin)
    for method, suffix in (("PATCH", "status"), ("POST", "assign")):
        resp = app.documents(make_event(method, f"{DOCS}/{doc.id}/{suffix}", token, via=via, body=bad))
        assert resp["statusCode"] == 400, describe(resp)
        assert error_message(resp) == expected, describe(resp)


def test_assign_with_no_recipient_is_400(app):
    doc = add_docs(app, [SEED_BY_EMAIL["head1@tu.ac.th"]])[0]
    token = token_for(SEED_BY_EMAIL["staff1@tu.ac.th"])
    resp = app.documents(make_event("POST", f"{DOCS}/{doc.id}/assign", token, body={"recipients": []}))
    assert resp["statusCode"] == 400, describe(resp)
    assert error_message(resp) == "Must assign at least one recipient user ID"


@PBT
@given(content_type=st.sampled_from(["application/json", "text/plain", None]), via=tokens_via)
def test_registration_without_multipart_content_type_is_400(app, content_type, via):
    reset(app)
    headers = {"content-type": content_type} if content_type else {}
    resp = app.documents(
        make_event(
            "POST",
            DOCS,
            token_for(SEED_BY_EMAIL["staff1@tu.ac.th"]),
            via=via,
            body={"title": "T"},
            headers=headers,
        )
    )
    assert resp["statusCode"] == 400, describe(resp)
    assert error_message(resp) == "Content-Type must be multipart/form-data"


def test_registration_form_errors_are_400(app):
    token = token_for(SEED_BY_EMAIL["staff1@tu.ac.th"])

    content_type, body = make_multipart(
        {"title": "T", "document_date": "2025-01-15", "sender": "S"}
    )
    resp = app.documents(
        make_event("POST", DOCS, token, body=body, headers={"content-type": content_type})
    )
    assert resp["statusCode"] == 400, describe(resp)
    assert error_message(resp) == "Missing required fields: receiver"

    boundary = "zz"
    resp = app.documents(
        make_event(
            "POST",
            DOCS,
            token,
            body=f"--{boundary}--\r\n",
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        )
    )
    assert resp["statusCode"] == 400, describe(resp)
    assert error_message(resp) == "Missing uploaded file"
    assert app.repo._docs == {}


@pytest.mark.parametrize("origin", [None, "https://app.example"])
def test_options_preflight_returns_204_with_cors_headers_and_needs_no_token(
    app, auth_login, monkeypatch, origin
):
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    headers = {"origin": origin} if origin else {}
    allow_origin = origin or "*"
    auth_handler = auth_login(FakeCognitoClient())

    for handler, path, cors in (
        (app.documents, DOCS, DOCUMENTS_CORS),
        (app.search, TRIE, SEARCH_CORS),
        (auth_handler, "/api/v1/auth/login", AUTH_CORS),
    ):
        resp = handler(make_event("OPTIONS", path, headers=headers))
        assert resp["statusCode"] == 204, describe(resp)
        assert resp["body"] == ""
        assert resp["headers"] == {**cors, "Access-Control-Allow-Origin": allow_origin}


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", DOCS),
        ("GET", f"{DOCS}/doc-1"),
        ("GET", f"{DOCS}/doc-1/file"),
        ("PATCH", f"{DOCS}/doc-1/status"),
        ("POST", f"{DOCS}/doc-1/assign"),
        ("POST", DOCS),
    ],
)
def test_documents_routes_without_a_token_are_401(app, method, path):
    resp = app.documents(make_event(method, path, body={"status": "ASSIGNED"}))
    assert resp["statusCode"] == 401, describe(resp)
    assert body_of(resp) == {"error": {"message": AUTH_REQUIRED}}


def test_search_without_a_token_is_401(app):
    resp = app.search(make_event("GET", TRIE))
    assert resp["statusCode"] == 401, describe(resp)
    assert body_of(resp) == {"error": {"message": AUTH_REQUIRED}}


def test_auth_me_without_a_token_is_401(auth_login):
    handler = auth_login(FakeCognitoClient())
    resp = handler(make_event("GET", "/api/v1/auth/me"))
    assert resp["statusCode"] == 401, describe(resp)
    assert body_of(resp) == {"error": {"message": AUTH_REQUIRED}}


# ---------------------------------------------------------------------------
# Token lookup order and format (Req 3.5)
# ---------------------------------------------------------------------------

def test_bearer_header_wins_over_cookie_and_both_header_spellings_work(app):
    add_docs(app, [SEED_BY_EMAIL["head1@tu.ac.th"]])
    good = token_for(SEED_BY_EMAIL["staff1@tu.ac.th"])

    event = make_event("GET", DOCS, good)
    event["cookies"] = ["auth_token=not-a-valid-token"]
    resp = app.documents(event)
    assert resp["statusCode"] == 200, describe(resp)

    event = make_event("GET", DOCS)
    event["headers"] = {"Authorization": f"Bearer {good}"}
    resp = app.documents(event)
    assert resp["statusCode"] == 200, describe(resp)


def test_cookie_header_with_other_cookies_and_cookie_attributes(app):
    add_docs(app, [SEED_BY_EMAIL["head1@tu.ac.th"]])
    good = token_for(SEED_BY_EMAIL["staff1@tu.ac.th"])

    event = make_event("GET", DOCS)
    event["headers"] = {"Cookie": f"theme=dark; auth_token={good}; lang=th"}
    resp = app.documents(event)
    assert resp["statusCode"] == 200, describe(resp)

    event = make_event("GET", DOCS)
    event["cookies"] = ["theme=dark", f"auth_token={good}; Path=/; HttpOnly"]
    resp = app.documents(event)
    assert resp["statusCode"] == 200, describe(resp)
