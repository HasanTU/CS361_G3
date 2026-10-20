"""AWS Lambda entrypoint handler for Auth Micro-Lambda."""

import base64
import json
import os
from typing import Any, Optional

from backend_v2.functions.auth.use_cases import AuthUseCases

_use_cases: Optional[AuthUseCases] = None
_current_origin: str = "*"


def get_use_cases() -> AuthUseCases:
    """Lazily initialize and return the AuthUseCases orchestrator."""
    global _use_cases
    if _use_cases is None:
        _use_cases = AuthUseCases()
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


def _extract_cookie_token(event: dict[str, Any]) -> Optional[str]:
    """Extract auth_token from HTTP API cookies array or headers."""
    raw_cookies = event.get("cookies") or []
    for item in raw_cookies:
        if isinstance(item, str) and item.startswith("auth_token="):
            return item.split("auth_token=", 1)[1].split(";", 1)[0].strip()

    headers = event.get("headers") or {}
    cookie_str = headers.get("cookie") or headers.get("Cookie") or ""
    if cookie_str:
        for part in cookie_str.split(";"):
            part = part.strip()
            if part.startswith("auth_token="):
                return part.split("auth_token=", 1)[1].strip()

    auth_header = headers.get("authorization") or headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        return auth_header.split("Bearer ", 1)[1].strip()

    return None


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode unverified JWT claims safely without external dependencies."""
    parts = token.strip().split(".")
    if len(parts) < 2:
        raise ValueError("Malformed JWT structure")
    payload_b64 = parts[1]
    rem = len(payload_b64) % 4
    if rem > 0:
        payload_b64 += "=" * (4 - rem)
    decoded_bytes = base64.urlsafe_b64decode(payload_b64)
    return json.loads(decoded_bytes.decode("utf-8"))


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

    use_cases = get_use_cases()

    try:
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
            users = use_cases.list_users()
            return _json_response(200, {"users": users})

        # GET /api/v1/auth/me
        if method == "GET" and (
            raw_path == "/api/v1/auth/me" or raw_path.endswith("/auth/me")
        ):
            token = _extract_cookie_token(event)
            if not token:
                return _error_response(401, "Authentication is required")
            claims = _decode_jwt_payload(token)
            return _json_response(200, {"user": claims})

        return _error_response(404, f"Route not found: {method} {raw_path}")

    except PermissionError as err:
        return _error_response(401, str(err))
    except ValueError as err:
        return _error_response(400, str(err))
    except Exception as err:
        return _error_response(500, f"Internal server error: {str(err)}")
