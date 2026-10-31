"""Read the token from an API Gateway HTTP API v2 event and turn it into a Caller."""
from typing import Any, Optional

from backend_v2.shared.auth.caller import Caller, caller_from_claims
from backend_v2.shared.auth.errors import AUTH_REQUIRED_MESSAGE, AuthenticationError
from backend_v2.shared.auth.token_verifier import CognitoTokenVerifier

_verifier: Optional[CognitoTokenVerifier] = None


def get_token_verifier() -> CognitoTokenVerifier:
    """Return the shared verifier, building it from the environment on first use."""
    global _verifier
    if _verifier is None:
        _verifier = CognitoTokenVerifier.from_env()
    return _verifier


def set_token_verifier(verifier: Optional[CognitoTokenVerifier]) -> None:
    """Install a verifier (tests). Passing None resets the cached verifier."""
    global _verifier
    _verifier = verifier


def extract_token(event: dict[str, Any]) -> Optional[str]:
    """Find the auth token: Bearer header, then event["cookies"], then the Cookie header."""
    headers = event.get("headers") or {}

    # 1. Authorization Bearer header
    auth_header = headers.get("authorization") or headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        return auth_header.split("Bearer ", 1)[1].strip() or None

    # 2. API Gateway v2 event["cookies"] list
    raw_cookies = event.get("cookies") or []
    for item in raw_cookies:
        if isinstance(item, str) and item.startswith("auth_token="):
            return item.split("auth_token=", 1)[1].split(";", 1)[0].strip()

    # 3. Cookie header
    cookie_str = headers.get("cookie") or headers.get("Cookie") or ""
    if cookie_str:
        for part in cookie_str.split(";"):
            part = part.strip()
            if part.startswith("auth_token="):
                return part.split("auth_token=", 1)[1].strip()

    return None


def authenticate(
    event: dict[str, Any], verifier: Optional[CognitoTokenVerifier] = None
) -> Caller:
    """Verify the request token and return the caller. Raises AuthenticationError."""
    token = extract_token(event)
    if not token:
        raise AuthenticationError(AUTH_REQUIRED_MESSAGE)
    claims = (verifier or get_token_verifier()).verify(token)
    return caller_from_claims(claims)
