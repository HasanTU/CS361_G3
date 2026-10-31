"""find_by_assigned_user sends an exact `sub` query and never touches `receiver`."""
import pytest

from backend_v2.shared.adapters.mongo_document_repository import MongoDocumentRepository


class FakeCollection:
    def __init__(self):
        self.find_calls = []

    def find(self, query):
        self.find_calls.append(query)
        return []


@pytest.fixture
def repo_and_collection():
    repo = MongoDocumentRepository(mongo_uri="mongodb://unused.invalid:27017/e_mailbox")
    fake = FakeCollection()
    repo._collection = fake
    return repo, fake


def test_exact_sub_query(repo_and_collection):
    repo, fake = repo_and_collection
    assert repo.find_by_assigned_user("abc") == []
    assert fake.find_calls == [{"assigned_to": "abc"}]


def test_sub_is_stripped_but_case_is_kept(repo_and_collection):
    repo, fake = repo_and_collection
    repo.find_by_assigned_user("  AbC-1 ")
    assert fake.find_calls == [{"assigned_to": "AbC-1"}]


@pytest.mark.parametrize("value", ["", "  ", None, ["abc"]])
def test_invalid_input_returns_empty_without_query(repo_and_collection, value):
    repo, fake = repo_and_collection
    assert repo.find_by_assigned_user(value) == []
    assert fake.find_calls == []
