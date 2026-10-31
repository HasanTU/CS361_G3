# Feature: backend-v2-auth-bypass-fix, Property 3
"""Role consistency: one role rule everywhere.

For any verified claims set, the role in the login response, the admin
behavior of the documents handler and the role derived by caller_from_claims
all equal "admin" exactly when "ADMIN" is an element of cognito:groups. Other
claims (email, name, cognito:username, custom:role, role, sub) never matter.
"""
import string

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from conftest import (
    FakeCognitoClient,
    body_of,
    default_claims,
    describe,
    make_event,
    make_token,
    seed_document,
)
from backend_v2.shared.auth import caller_from_claims

PBT = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

DOCS = "/api/v1/documents"
LOGIN = "/api/v1/auth/login"
GROUP_CHOICES = ["ADMIN", "USER", "STAFF", "admin", "Admin"]

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

ADMINISH = ["admin", "Admin", "ADMIN", "staff", "Staff", "Admin Staff", "administrator"]

# A non-blank sub made of URL-safe characters, sometimes admin-looking.
subs = st.one_of(
    st.text(alphabet=string.ascii_letters + string.digits + "-_", min_size=1, max_size=24),
    st.sampled_from(ADMINISH),
)

words = st.text(
    alphabet=string.ascii_letters + string.digits + " .@-_",
    max_size=30,
)
texts = st.one_of(
    words,
    st.sampled_from(ADMINISH),
    st.tuples(st.sampled_from(ADMINISH), words).map(lambda pair: f"{pair[0]} {pair[1]}"),
    st.tuples(words, st.sampled_from(ADMINISH)).map(lambda pair: f"{pair[0]}{pair[1]}@tu.ac.th"),
)

groups = st.lists(st.sampled_from(GROUP_CHOICES), unique=True, max_size=len(GROUP_CHOICES))


@st.composite
def claim_overrides(draw):
    """Claim overrides for a validly signed token. A None value omits the claim."""
    return {
        "sub": draw(subs),
        "email": draw(st.one_of(st.none(), texts)),
        "name": draw(st.one_of(st.none(), texts)),
        "cognito:groups": draw(groups),
        "custom:role": draw(st.sampled_from([None, "admin"])),
        "role": draw(st.sampled_from([None, "admin"])),
        "cognito:username": draw(st.one_of(st.none(), texts)),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def reset(app):
    app.repo._docs.clear()
    app.repo._counter = 0


def login_role(auth_login, token):
    handler = auth_login(FakeCognitoClient(id_token=token))
    resp = handler(
        make_event("POST", LOGIN, body={"email": "caller@example.com", "password": "pw"})
    )
    assert resp["statusCode"] == 200, describe(resp)
    return body_of(resp)["user"]["role"]


def documents_show_unassigned(app, token):
    """True when the documents handler lists a document assigned to nobody."""
    reset(app)
    doc = seed_document(
        app.repo, app.storage, assigned_to=[], receiver="Nobody", title="Unassigned"
    )
    resp = app.documents(make_event("GET", DOCS, token))
    assert resp["statusCode"] == 200, describe(resp)
    return doc.id in {d["id"] for d in body_of(resp)["documents"]}


def all_three(app, auth_login, overrides):
    """Return (login role, unassigned doc visible, caller_from_claims role)."""
    token = make_token(overrides)
    return (
        login_role(auth_login, token),
        documents_show_unassigned(app, token),
        caller_from_claims(default_claims(overrides)).role,
    )


# ---------------------------------------------------------------------------
# Property 3
# ---------------------------------------------------------------------------

@PBT
@given(overrides=claim_overrides())
def test_role_is_admin_exactly_when_admin_group_is_present(app, auth_login, overrides):
    expected_admin = "ADMIN" in overrides["cognito:groups"]

    role, sees_unassigned, derived_role = all_three(app, auth_login, overrides)

    assert (role == "admin") is expected_admin, f"login role={role!r} for {overrides}"
    assert sees_unassigned is expected_admin, f"documents handler for {overrides}"
    assert (derived_role == "admin") is expected_admin, f"caller role={derived_role!r} for {overrides}"
    # The role strings use one vocabulary.
    assert role == derived_role == ("admin" if expected_admin else "user")


# ---------------------------------------------------------------------------
# Examples: admin-looking values never grant admin without the ADMIN group
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "extra",
    [
        {"email": "Admin Staff"},
        {"name": "Admin Staff"},
        {"email": "admin.staff@tu.ac.th", "name": "Admin Staff"},
        {"custom:role": "admin"},
        {"role": "admin"},
        {"cognito:username": "admin"},
        {"sub": "admin"},
        {
            "email": "Admin Staff",
            "name": "Admin Staff",
            "custom:role": "admin",
            "role": "admin",
            "cognito:username": "admin",
        },
    ],
    ids=lambda extra: "+".join(sorted(extra)),
)
@pytest.mark.parametrize("group_list", [[], ["USER"], ["STAFF"], ["admin"], ["Admin"], ["USER", "STAFF", "admin", "Admin"]])
def test_admin_looking_values_without_admin_group_never_grant_admin(
    app, auth_login, extra, group_list
):
    overrides = {"cognito:groups": group_list, **extra}

    role, sees_unassigned, derived_role = all_three(app, auth_login, overrides)

    assert role == "user"
    assert sees_unassigned is False
    assert derived_role == "user"


@pytest.mark.parametrize("group_list", [["ADMIN"], ["USER", "ADMIN"]])
def test_admin_group_grants_admin_in_all_three_places(app, auth_login, group_list):
    overrides = {
        "cognito:groups": group_list,
        "email": "plain@example.com",
        "name": "Plain Person",
    }

    role, sees_unassigned, derived_role = all_three(app, auth_login, overrides)

    assert role == "admin"
    assert sees_unassigned is True
    assert derived_role == "admin"
