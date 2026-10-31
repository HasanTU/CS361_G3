# Feature: backend-v2-auth-bypass-fix, directory user and recipient resolution
import dataclasses

import pytest

from backend_v2.shared.domain.directory_user import (
    DirectoryUser,
    resolve_recipient,
    resolve_recipients,
)

A = DirectoryUser("00000000-0000-4000-8000-00000000000a", "alice@tu.ac.th", "Alice Smith")
B = DirectoryUser("00000000-0000-4000-8000-00000000000b", "Bob@tu.ac.th", "Bob Jones")
C1 = DirectoryUser("00000000-0000-4000-8000-0000000000c1", "c1@tu.ac.th", "Same Name")
C2 = DirectoryUser("00000000-0000-4000-8000-0000000000c2", "c2@tu.ac.th", "same name")
USERS = [A, B, C1, C2]


def test_directory_user_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        A.sub = "x"  # type: ignore[misc]


def test_resolve_by_sub():
    assert resolve_recipient(A.sub, USERS) is A


def test_sub_is_exact_and_case_sensitive():
    with pytest.raises(ValueError, match="Unknown recipient"):
        resolve_recipient(A.sub.upper(), USERS)


@pytest.mark.parametrize("value", ["bob@tu.ac.th", "BOB@TU.AC.TH", "  Bob@tu.ac.th  "])
def test_resolve_by_email_any_case(value):
    assert resolve_recipient(value, USERS) is B


def test_resolve_by_unique_name():
    assert resolve_recipient("  alice smith ", USERS) is A


def test_sub_wins_over_email_and_name():
    tricky = DirectoryUser("alice@tu.ac.th", "other@tu.ac.th", "Other")
    assert resolve_recipient("alice@tu.ac.th", [A, tricky]) is tricky


def test_ambiguous_name():
    with pytest.raises(ValueError) as exc:
        resolve_recipient("Same Name", USERS)
    assert str(exc.value) == "Recipient name is ambiguous: Same Name"


def test_unknown_recipient():
    with pytest.raises(ValueError) as exc:
        resolve_recipient(" Nobody ", USERS)
    assert str(exc.value) == "Unknown recipient: Nobody"


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_recipient(value):
    with pytest.raises(ValueError) as exc:
        resolve_recipient(value, USERS)
    assert str(exc.value) == "Recipient is required"


@pytest.mark.parametrize("value", [None, 5, ["alice@tu.ac.th"], b"alice"])
def test_non_string_recipient(value):
    with pytest.raises(ValueError) as exc:
        resolve_recipient(value, USERS)
    assert str(exc.value) == "Recipient must be a string"


def test_resolve_recipients_keeps_order():
    assert resolve_recipients([B.sub, "Alice Smith"], USERS) == [B, A]


def test_resolve_recipients_drops_later_duplicates():
    assert resolve_recipients(["alice@tu.ac.th", A.sub, "Alice Smith", B.sub], USERS) == [A, B]


def test_resolve_recipients_empty_list():
    with pytest.raises(ValueError) as exc:
        resolve_recipients([], USERS)
    assert str(exc.value) == "Must assign at least one recipient user ID"


def test_resolve_recipients_propagates_item_error():
    with pytest.raises(ValueError, match="Unknown recipient: ghost"):
        resolve_recipients([A.sub, "ghost"], USERS)
