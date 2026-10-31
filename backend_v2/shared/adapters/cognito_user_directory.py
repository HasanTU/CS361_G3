"""Cognito implementation of the user directory port."""

import os
import time
from typing import Any, Callable, Optional

from backend_v2.shared.domain.directory_user import DirectoryUser
from backend_v2.shared.ports.user_directory import UserDirectoryPort


class CognitoUserDirectory(UserDirectoryPort):
    """Lists enabled users of a Cognito user pool, cached for a short time."""

    def __init__(
        self,
        user_pool_id: Optional[str] = None,
        cognito_client: Any = None,
        cache_ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._pool_id = user_pool_id or os.getenv("COGNITO_USER_POOL_ID", "")
        self._client = cognito_client
        self._ttl = cache_ttl_seconds
        self._clock = clock
        self._cache: Optional[list[DirectoryUser]] = None
        self._cached_at = 0.0

    def list_users(self) -> list[DirectoryUser]:
        if not self._pool_id:
            raise RuntimeError("COGNITO_USER_POOL_ID is missing")

        if self._cache is not None and (self._clock() - self._cached_at) < self._ttl:
            return list(self._cache)

        client = self._client
        if client is None:
            import boto3

            client = boto3.client(
                "cognito-idp", region_name=self._pool_id.split("_", 1)[0]
            )

        users: list[DirectoryUser] = []
        paginator = client.get_paginator("list_users")
        for page in paginator.paginate(UserPoolId=self._pool_id):
            for raw in page.get("Users", []):
                if raw.get("Enabled") is False:
                    continue
                attrs = {a["Name"]: a["Value"] for a in raw.get("Attributes", [])}
                sub = attrs.get("sub")
                if not sub:
                    continue
                email = attrs.get("email", "")
                name = attrs.get("name") or email.split("@")[0] or sub
                users.append(DirectoryUser(sub=sub, email=email, name=name))

        users.sort(key=lambda u: (u.name.casefold(), u.sub))
        self._cache = users
        self._cached_at = self._clock()
        return list(users)
