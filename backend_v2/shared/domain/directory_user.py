"""Directory user and recipient resolution (standard library only)."""

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class DirectoryUser:
    """One enabled user in the Cognito user pool."""

    sub: str
    email: str
    name: str


def resolve_recipient(value: Any, users: Sequence[DirectoryUser]) -> DirectoryUser:
    """Resolve a typed recipient to a directory user.

    Order: exact sub, then email (case-insensitive), then a display name that
    matches exactly one user.
    """
    if not isinstance(value, str):
        raise ValueError("Recipient must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Recipient is required")

    for user in users:
        if user.sub == cleaned:
            return user

    folded = cleaned.casefold()
    for user in users:
        if user.email.casefold() == folded:
            return user

    named = [user for user in users if user.name.strip().casefold() == folded]
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        raise ValueError(f"Recipient name is ambiguous: {cleaned}")

    raise ValueError(f"Unknown recipient: {cleaned}")


def resolve_recipients(
    values: Sequence[Any], users: Sequence[DirectoryUser]
) -> list[DirectoryUser]:
    """Resolve several recipients in order, dropping later duplicates by sub."""
    if not values:
        raise ValueError("Must assign at least one recipient user ID")

    resolved: list[DirectoryUser] = []
    seen: set[str] = set()
    for value in values:
        user = resolve_recipient(value, users)
        if user.sub in seen:
            continue
        seen.add(user.sub)
        resolved.append(user)
    return resolved
