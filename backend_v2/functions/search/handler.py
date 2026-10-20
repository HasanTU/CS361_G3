"""AWS Lambda entrypoint handler for Search Micro-Lambda."""

import base64
import json
import os
from typing import Any, Optional, Tuple

from backend_v2.functions.search.use_cases import SearchUseCases

# Module-level lazy singletons for connection reuse across warm invocations
_repository: Any = None
_use_cases: Optional[SearchUseCases] = None


def get_use_cases() -> SearchUseCases:
    """Lazily initialize and return the SearchUseCases orchestrator."""
    global _repository, _use_cases
    if _use_cases is None:
        from backend_v2.shared.adapters.mongo_document_repository import (
            MongoDocumentRepository,
        )

        _repository = MongoDocumentRepository()
        _use_cases = SearchUseCases(repository=_repository)
    return _use_cases


def set_use_cases(use_cases: SearchUseCases) -> None:
    """Override use cases instance (for offline unit testing and mocking)."""
    global _use_cases
    _use_cases = use_cases


_current_origin: str = "*"


def _cors_headers() -> dict[str, str]:
    """Generate standard CORS headers using configured frontend origin."""
    allow_origin = _current_origin or os.getenv("FRONTEND_ORIGIN", "*")
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": allow_origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,Cookie",
        "Access-Control-Allow-Methods": "GET,OPTIONS",
    }


def _json_response(status_code: int, body: Any) -> dict[str, Any]:
    """Format a standard JSON response for API Gateway HTTP API v2."""
    return {
        "statusCode": status_code,
        "headers": _cors_headers(),
        "body": json.dumps(body, ensure_ascii=False),
    }


def _error_response(status_code: int, message: str) -> dict[str, Any]:
    """Format a standard error response envelope."""
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
    """Decode unverified JWT payload safely without external dependencies."""
    parts = token.strip().split(".")
    if len(parts) < 2:
        raise ValueError("Malformed JWT structure")
    payload_b64 = parts[1]
    rem = len(payload_b64) % 4
    if rem > 0:
        payload_b64 += "=" * (4 - rem)
    decoded_bytes = base64.urlsafe_b64decode(payload_b64)
    return json.loads(decoded_bytes.decode("utf-8"))


def _parse_auth(event: dict[str, Any]) -> Tuple[list[str], bool]:
    """Extract authenticated user identity candidates and role from Cognito token."""
    token = _extract_cookie_token(event)
    if not token:
        raise PermissionError("Authentication is required")

    try:
        claims = _decode_jwt_payload(token)
    except Exception as err:
        raise PermissionError(f"Invalid authentication token: {err}") from err

    raw_candidates = [
        claims.get("email"),
        claims.get("name"),
        claims.get("cognito:username"),
        claims.get("username"),
        claims.get("sub"),
    ]
    identities = [str(x).strip() for x in raw_candidates if x and str(x).strip()]
    if not identities:
        raise PermissionError("Token contains no valid user identity")

    groups = [str(g).upper() for g in claims.get("cognito:groups", [])]
    role = str(claims.get("custom:role") or claims.get("role") or "").upper()
    email = str(claims.get("email") or "").lower()
    is_admin = (
        ("ADMIN" in groups)
        or (role == "ADMIN")
        or email.startswith("admin")
        or email in ["admin1@tu.ac.th", "admin2@tu.ac.th"]
    )

    return identities, is_admin


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """AWS Lambda entrypoint handler for Search Micro-Lambda."""
    global _current_origin
    req_headers = event.get("headers") or {}
    _current_origin = req_headers.get("origin") or req_headers.get("Origin") or os.getenv("FRONTEND_ORIGIN", "*")

    http_ctx = event.get("requestContext", {}).get("http", {})
    method = (http_ctx.get("method") or event.get("httpMethod") or "GET").upper()
    raw_path = event.get("rawPath") or http_ctx.get("path") or ""

    # 1. Handle CORS Preflight
    if method == "OPTIONS":
        return {
            "statusCode": 204,
            "headers": _cors_headers(),
            "body": "",
        }

    use_cases = get_use_cases()

    try:
        # 2. Authenticate request
        user_identities, is_admin = _parse_auth(event)

        # 3. Route Dispatch: GET /api/v1/search/trie
        if method == "GET" and (
            raw_path == "/api/v1/search/trie" or raw_path.endswith("/search/trie")
        ):
            trie_dict, count = use_cases.get_search_trie(
                user_id=user_identities, is_admin=is_admin
            )
            return _json_response(200, {
                "trie": trie_dict,
                "count": count,
            })

        return _error_response(404, f"Route not found: {method} {raw_path}")

    except PermissionError as err:
        status = 401 if "Authentication is required" in str(err) or "Invalid authentication token" in str(err) else 403
        return _error_response(status, str(err))
    except Exception as err:
        return _error_response(500, f"Internal server error: {str(err)}")
