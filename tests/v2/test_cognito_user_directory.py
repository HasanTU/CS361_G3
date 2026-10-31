# Feature: backend-v2-auth-bypass-fix, task 3.5
import pytest

from backend_v2.shared.adapters.cognito_user_directory import CognitoUserDirectory
from backend_v2.shared.domain.directory_user import DirectoryUser

POOL_ID = "us-east-1_TestPool1"


def _user(sub, email=None, name=None, enabled=True):
    attrs = []
    if sub is not None:
        attrs.append({"Name": "sub", "Value": sub})
    if email is not None:
        attrs.append({"Name": "email", "Value": email})
    if name is not None:
        attrs.append({"Name": "name", "Value": name})
    return {"Enabled": enabled, "Attributes": attrs}


class _Paginator:
    def __init__(self, client):
        self._client = client

    def paginate(self, UserPoolId):
        self._client.paginate_calls.append(UserPoolId)
        return iter(self._client.pages)


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.paginate_calls = []
        self.paginator_names = []

    def get_paginator(self, name):
        self.paginator_names.append(name)
        return _Paginator(self)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _pages():
    return [
        {
            "Users": [
                _user("sub-b", "bob@tu.ac.th", "Bob"),
                _user("sub-off", "off@tu.ac.th", "Off", enabled=False),
                _user(None, "nosub@tu.ac.th", "NoSub"),
            ]
        },
        {
            "Users": [
                _user("sub-a", "alice@tu.ac.th", "alice"),
                _user("sub-c", "carol@tu.ac.th"),
                _user("sub-b2", "bob2@tu.ac.th", "Bob"),
            ]
        },
    ]


def test_lists_users_from_all_pages_skips_disabled_and_missing_sub_and_sorts():
    client = FakeClient(_pages())
    directory = CognitoUserDirectory(user_pool_id=POOL_ID, cognito_client=client)

    users = directory.list_users()

    assert users == [
        DirectoryUser("sub-a", "alice@tu.ac.th", "alice"),
        DirectoryUser("sub-b", "bob@tu.ac.th", "Bob"),
        DirectoryUser("sub-b2", "bob2@tu.ac.th", "Bob"),
        DirectoryUser("sub-c", "carol@tu.ac.th", "carol"),
    ]
    assert client.paginator_names == ["list_users"]
    assert client.paginate_calls == [POOL_ID]


def test_second_call_within_ttl_uses_cache_and_returns_a_copy():
    client = FakeClient(_pages())
    clock = Clock()
    directory = CognitoUserDirectory(
        user_pool_id=POOL_ID, cognito_client=client, cache_ttl_seconds=300.0, clock=clock
    )

    first = directory.list_users()
    first.clear()
    clock.now += 299
    second = directory.list_users()

    assert len(client.paginate_calls) == 1
    assert len(second) == 4


def test_call_after_ttl_queries_client_again():
    client = FakeClient(_pages())
    clock = Clock()
    directory = CognitoUserDirectory(
        user_pool_id=POOL_ID, cognito_client=client, cache_ttl_seconds=300.0, clock=clock
    )

    directory.list_users()
    clock.now += 301
    directory.list_users()

    assert len(client.paginate_calls) == 2


def test_missing_pool_id_raises_runtime_error(monkeypatch):
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    directory = CognitoUserDirectory(cognito_client=FakeClient([]))

    with pytest.raises(RuntimeError, match="COGNITO_USER_POOL_ID is missing"):
        directory.list_users()


def test_pool_id_is_read_from_env(monkeypatch):
    monkeypatch.setenv("COGNITO_USER_POOL_ID", POOL_ID)
    client = FakeClient([])
    directory = CognitoUserDirectory(cognito_client=client)

    assert directory.list_users() == []
    assert client.paginate_calls == [POOL_ID]
