"""AWS Lambda entrypoint handler for Search Micro-Lambda."""

import json
import logging
import os
from typing import Any, Optional

from backend_v2.functions.search.use_cases import SearchUseCases
from backend_v2.shared.auth import (
    INTERNAL_ERROR_MESSAGE,
    SERVICE_UNAVAILABLE_MESSAGE,
    AuthenticationError,
    AuthServiceUnavailableError,
    authenticate,
)

logger = logging.getLogger(__name__)

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

    try:
        use_cases = get_use_cases()

        # 2. Authenticate request (verified Cognito ID token)
        caller = authenticate(event)

        # 3. Route Dispatch: GET /api/v1/search/trie
        if method == "GET" and (
            raw_path == "/api/v1/search/trie" or raw_path.endswith("/search/trie")
        ):
            trie_dict, count = use_cases.get_search_trie(
                caller_sub=caller.sub, is_admin=caller.is_admin
            )
            return _json_response(200, {
                "trie": trie_dict,
                "count": count,
            })

        return _error_response(404, f"Route not found: {method} {raw_path}")

    except AuthenticationError as err:
        return _error_response(401, str(err))
    except AuthServiceUnavailableError:
        return _error_response(503, SERVICE_UNAVAILABLE_MESSAGE)
    except PermissionError as err:
        return _error_response(403, str(err))
    except Exception:
        logger.exception("Unhandled error in search handler")
        return _error_response(500, INTERNAL_ERROR_MESSAGE)
