"""Shared authentication for the backend_v2 Lambdas."""
from backend_v2.shared.auth.caller import ADMIN_GROUP, Caller, caller_from_claims, require_admin
from backend_v2.shared.auth.errors import (
    AUTH_REQUIRED_MESSAGE,
    INTERNAL_ERROR_MESSAGE,
    INVALID_TOKEN_MESSAGE,
    SERVICE_UNAVAILABLE_MESSAGE,
    AuthenticationError,
    AuthServiceUnavailableError,
)
from backend_v2.shared.auth.request_auth import (
    authenticate,
    extract_token,
    get_token_verifier,
    set_token_verifier,
)
from backend_v2.shared.auth.token_verifier import CognitoTokenVerifier

__all__ = [
    "AUTH_REQUIRED_MESSAGE",
    "INVALID_TOKEN_MESSAGE",
    "SERVICE_UNAVAILABLE_MESSAGE",
    "INTERNAL_ERROR_MESSAGE",
    "AuthenticationError",
    "AuthServiceUnavailableError",
    "ADMIN_GROUP",
    "Caller",
    "caller_from_claims",
    "require_admin",
    "CognitoTokenVerifier",
    "extract_token",
    "authenticate",
    "get_token_verifier",
    "set_token_verifier",
]
