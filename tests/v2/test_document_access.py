# Feature: backend-v2-auth-bypass-fix, Document.is_accessible_by uses sub only
import pytest

from backend_v2.shared.domain.document import Document

SUB = "00000000-0000-4000-8000-0000000000aa"
OTHER_SUB = "00000000-0000-4000-8000-0000000000bb"


def make_doc(assigned_to=None, receiver="Alice Smith"):
    return Document(
        title="T",
        document_date="2025-01-01",
        sender="Sender",
        receiver=receiver,
        file_path="p/f.pdf",
        file_name="f.pdf",
        file_type="application/pdf",
        file_size=1,
        assigned_to=assigned_to,
    )


def test_admin_always_has_access():
    assert make_doc().is_accessible_by("anything", is_admin=True) is True
    assert make_doc().is_accessible_by("", is_admin=True) is True


def test_exact_sub_in_assigned_to_has_access():
    assert make_doc([SUB]).is_accessible_by(SUB) is True
    assert make_doc([OTHER_SUB, SUB]).is_accessible_by(f" {SUB} ") is True


def test_different_case_is_denied():
    doc = make_doc(["AbCdEf"])
    assert doc.is_accessible_by("abcdef") is False
    assert doc.is_accessible_by("ABCDEF") is False
    assert doc.is_accessible_by("AbCdEf") is True


def test_receiver_match_alone_is_denied():
    doc = make_doc([OTHER_SUB], receiver="Alice Smith")
    assert doc.is_accessible_by("Alice Smith") is False
    assert doc.is_accessible_by("alice smith") is False


def test_list_argument_is_denied():
    doc = make_doc([SUB])
    assert doc.is_accessible_by([SUB]) is False
    assert doc.is_accessible_by((SUB,)) is False


@pytest.mark.parametrize("value", ["", "   ", None])
def test_empty_or_blank_is_denied(value):
    assert make_doc([SUB]).is_accessible_by(value) is False
