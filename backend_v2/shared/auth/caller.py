"""The verified caller of a request, built from verified Cognito ID token claims."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from .errors import INVALID_TOKEN_MESSAGE, AuthenticationError

ADMIN_GROUP = "ADMIN"


@dataclass(frozen=True)
class Caller:
    sub: str
    email: str = ""
    name: str = ""
    groups: tuple[str, ...] = ()
    faculty: str = ""
    claims: dict[str, Any] = field(
        default_factory=dict, compare=False, repr=False, hash=False
    )

    @property
    def is_admin(self) -> bool:
        return ADMIN_GROUP in self.groups

    @property
    def role(self) -> str:
        return "admin" if self.is_admin else "user"

    @property
    def actor_label(self) -> str:
        return self.email or self.sub


def caller_from_claims(claims: Mapping[str, Any]) -> Caller:
    """Build a Caller from verified claims.

    Only sub, email, name, cognito:groups and custom:faculty are read. Claims such
    as custom:role, role, cognito:username and username never influence the result.
    """
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise AuthenticationError(INVALID_TOKEN_MESSAGE)

    raw_groups = claims.get("cognito:groups")
    groups = tuple(str(g) for g in raw_groups) if isinstance(raw_groups, list) else ()

    return Caller(
        sub=sub,
        email=str(claims.get("email") or "").strip(),
        name=str(claims.get("name") or "").strip(),
        groups=groups,
        faculty=str(claims.get("custom:faculty") or "").strip(),
        claims=dict(claims),
    )


def require_admin(caller: Caller, message: str) -> None:
    if not caller.is_admin:
        raise PermissionError(message)
