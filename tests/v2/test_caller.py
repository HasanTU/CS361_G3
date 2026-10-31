# Feature: backend-v2-auth-bypass-fix, task 3.1
import dataclasses

import pytest

from backend_v2.shared.auth.caller import (
    ADMIN_GROUP,
    Caller,
    caller_from_claims,
    require_admin,
)
from backend_v2.shared.auth.errors import (
    AUTH_REQUIRED_MESSAGE,
    INTERNAL_ERROR_MESSAGE,
    INVALID_TOKEN_MESSAGE,
    SERVICE_UNAVAILABLE_MESSAGE,
    AuthenticationError,
    AuthServiceUnavailableError,
)


def test_messages_and_error_types():
    assert AUTH_REQUIRED_MESSAGE == "Authentication is required"
    assert INVALID_TOKEN_MESSAGE == "Invalid authentication token"
    assert SERVICE_UNAVAILABLE_MESSAGE == "Authentication service is unavailable"
    assert INTERNAL_ERROR_MESSAGE == "Internal server error"
    assert issubclass(AuthenticationError, PermissionError)
    assert not issubclass(AuthServiceUnavailableError, PermissionError)


def test_field_mapping():
    claims = {
        "sub": " 00000000-0000-4000-8000-000000000001 ",
        "email": " a@tu.ac.th ",
        "name": " Name A ",
        "cognito:groups": ["USER", "OTHER"],
        "custom:faculty": " Science ",
    }
    caller = caller_from_claims(claims)
    assert caller.sub == "00000000-0000-4000-8000-000000000001"
    assert caller.email == "a@tu.ac.th"
    assert caller.name == "Name A"
    assert caller.groups == ("USER", "OTHER")
    assert caller.faculty == "Science"
    assert caller.claims == claims


def test_defaults_when_optional_claims_missing():
    caller = caller_from_claims({"sub": "s1"})
    assert (caller.email, caller.name, caller.faculty) == ("", "", "")
    assert caller.groups == ()


def test_groups_ignored_when_not_a_list():
    assert caller_from_claims({"sub": "s1", "cognito:groups": "ADMIN"}).groups == ()
    assert caller_from_claims({"sub": "s1", "cognito:groups": None}).groups == ()


def test_admin_group_constant():
    assert ADMIN_GROUP == "ADMIN"


def test_is_admin_true_only_for_exact_admin_group():
    assert caller_from_claims({"sub": "s", "cognito:groups": ["ADMIN"]}).is_admin
    assert caller_from_claims({"sub": "s", "cognito:groups": ["USER", "ADMIN"]}).is_admin
    for group in ("admin", "Admin", "STAFF"):
        assert not caller_from_claims({"sub": "s", "cognito:groups": [group]}).is_admin
    assert not caller_from_claims({"sub": "s"}).is_admin


def test_role_values():
    assert caller_from_claims({"sub": "s", "cognito:groups": ["ADMIN"]}).role == "admin"
    assert caller_from_claims({"sub": "s", "cognito:groups": ["USER"]}).role == "user"


def test_actor_label_falls_back_to_sub():
    assert caller_from_claims({"sub": "s1", "email": "a@tu.ac.th"}).actor_label == "a@tu.ac.th"
    assert caller_from_claims({"sub": "s1"}).actor_label == "s1"
    assert caller_from_claims({"sub": "s1", "email": "  "}).actor_label == "s1"


@pytest.mark.parametrize("claims", [{}, {"sub": ""}, {"sub": "   "}, {"sub": None}])
def test_missing_or_blank_sub_raises(claims):
    with pytest.raises(AuthenticationError) as exc:
        caller_from_claims(claims)
    assert str(exc.value) == INVALID_TOKEN_MESSAGE


def test_role_claims_and_usernames_do_not_grant_admin():
    caller = caller_from_claims(
        {
            "sub": "s",
            "email": "admin@tu.ac.th",
            "name": "Admin Staff",
            "cognito:username": "admin",
            "username": "admin",
            "custom:role": "admin",
            "role": "admin",
            "cognito:groups": ["USER"],
        }
    )
    assert caller.is_admin is False
    assert caller.role == "user"
    assert caller_from_claims({"sub": "s", "custom:role": "admin"}).role == "user"


def test_repr_does_not_contain_claim_only_values():
    caller = caller_from_claims({"sub": "s", "secret-claim": "hidden@example.com"})
    assert "hidden@example.com" not in repr(caller)
    assert "claims" not in repr(caller)


def test_claims_excluded_from_equality_and_hash():
    a = caller_from_claims({"sub": "s", "x": 1})
    b = caller_from_claims({"sub": "s", "x": 2})
    assert a == b
    assert hash(a) == hash(b)


def test_caller_is_frozen():
    caller = Caller(sub="s")
    with pytest.raises(dataclasses.FrozenInstanceError):
        caller.sub = "other"


def test_require_admin():
    admin = Caller(sub="s", groups=("ADMIN",))
    user = Caller(sub="s", groups=("USER",))
    require_admin(admin, "nope")
    with pytest.raises(PermissionError) as exc:
        require_admin(user, "Admin privilege required")
    assert str(exc.value) == "Admin privilege required"
    assert not isinstance(exc.value, AuthenticationError)
