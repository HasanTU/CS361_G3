"""Authentication error types and the client-facing messages for backend_v2."""

AUTH_REQUIRED_MESSAGE = "Authentication is required"
INVALID_TOKEN_MESSAGE = "Invalid authentication token"
SERVICE_UNAVAILABLE_MESSAGE = "Authentication service is unavailable"
INTERNAL_ERROR_MESSAGE = "Internal server error"


class AuthenticationError(PermissionError):
    """Missing or invalid credentials. Handlers map it to HTTP 401."""


class AuthServiceUnavailableError(Exception):
    """Cognito or its JWKS endpoint cannot be reached. Handlers map it to HTTP 503."""
