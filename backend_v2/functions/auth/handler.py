"""AWS Lambda entrypoint handler for Auth Micro-Lambda."""

import json
import logging
import os
from typing import Any, Optional

from backend_v2.functions.auth.use_cases import AuthUseCases
from backend_v2.shared.auth import (
    INTERNAL_ERROR_MESSAGE,
    SERVICE_UNAVAILABLE_MESSAGE,
    AuthenticationError,
    AuthServiceUnavailableError,
    authenticate,
)

logger = logging.getLogger(__name__)

_use_cases: Optional[AuthUseCases] = None
_current_origin: str = "*"


def get_use_cases() -> AuthUseCases:
    """Lazily initialize and return the AuthUseCases orchestrator."""
    global _use_cases
    if _use_cases is None:
        from backend_v2.shared.adapters.cognito_user_directory import (
            CognitoUserDirectory,
        )

        _use_cases = AuthUseCases(user_directory=CognitoUserDirectory())
    return _use_cases


def set_use_cases(use_cases: AuthUseCases) -> None:
    """Override use cases instance (for offline unit testing and mocking)."""
    global _use_cases
    _use_cases = use_cases


def _cors_headers(cookie: Optional[str] = None) -> dict[str, str]:
    """Generate standard CORS headers including optional Set-Cookie."""
    allow_origin = _current_origin or os.getenv("FRONTEND_ORIGIN", "*")
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": allow_origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,Cookie",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    }
    if cookie:
        headers["Set-Cookie"] = cookie
    return headers


def _json_response(
    status_code: int, body: Any, cookie: Optional[str] = None
) -> dict[str, Any]:
    """Format standard JSON response for API Gateway HTTP API v2."""
    resp = {
        "statusCode": status_code,
        "headers": _cors_headers(cookie=cookie),
        "body": json.dumps(body, ensure_ascii=False),
    }
    # In API Gateway HTTP API v2, cookies can also be passed via the top-level 'cookies' array
    if cookie:
        resp["cookies"] = [cookie]
    return resp


def _error_response(status_code: int, message: str) -> dict[str, Any]:
    """Format standard error envelope."""
    return _json_response(status_code, {"error": {"message": message}})


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """AWS Lambda entrypoint handler for Auth Micro-Lambda."""
    global _current_origin
    req_headers = event.get("headers") or {}
    _current_origin = req_headers.get("origin") or req_headers.get("Origin") or os.getenv("FRONTEND_ORIGIN", "*")

    http_ctx = event.get("requestContext", {}).get("http", {})
    method = (http_ctx.get("method") or event.get("httpMethod") or "GET").upper()
    raw_path = event.get("rawPath") or http_ctx.get("path") or ""

    # 1. CORS Preflight
    if method == "OPTIONS":
        return {
            "statusCode": 204,
            "headers": _cors_headers(),
            "body": "",
        }

    try:
        use_cases = get_use_cases()

        # POST /api/v1/auth/login
        if method == "POST" and (
            raw_path == "/api/v1/auth/login" or raw_path.endswith("/auth/login")
        ):
            body = json.loads(event.get("body") or "{}")
            email = body.get("email", "")
            password = body.get("password", "")

            user, token = use_cases.login(email=email, password=password)

            # Issue HttpOnly, Secure cookie and return token in payload for Authorization header fallback
            cookie_val = (
                f"auth_token={token}; Path=/; HttpOnly; Secure; SameSite=None; Max-Age=86400"
            )
            return _json_response(200, {"user": user, "token": token}, cookie=cookie_val)

        # POST /api/v1/auth/logout
        if method == "POST" and (
            raw_path == "/api/v1/auth/logout" or raw_path.endswith("/auth/logout")
        ):
            clear_cookie = (
                "auth_token=; Path=/; HttpOnly; Secure; SameSite=None; Max-Age=0"
            )
            return _json_response(
                200, {"message": "Logged out successfully"}, cookie=clear_cookie
            )

        # GET /api/v1/auth/users
        if method == "GET" and (
            raw_path == "/api/v1/auth/users" or raw_path.endswith("/auth/users")
        ):
            authenticate(event)
            users = use_cases.list_users()
            return _json_response(200, {"users": users})

        # GET /api/v1/auth/me
        if method == "GET" and (
            raw_path == "/api/v1/auth/me" or raw_path.endswith("/auth/me")
        ):
            caller = authenticate(event)
            return _json_response(200, {"user": caller.claims})

        return _error_response(404, f"Route not found: {method} {raw_path}")

    except AuthenticationError as err:
        return _error_response(401, str(err))
    except AuthServiceUnavailableError:
        return _error_response(503, SERVICE_UNAVAILABLE_MESSAGE)
    except PermissionError as err:
        # Login: "Invalid email or password"
        return _error_response(401, str(err))
    except ValueError as err:
        return _error_response(400, str(err))
    except Exception:
        logger.exception("Unhandled error in auth handler")
        return _error_response(500, INTERNAL_ERROR_MESSAGE)
