"""AWS Lambda entrypoint handler for Documents Micro-Lambda."""

import base64
from email import message_from_bytes
from email.policy import default as default_policy
import json
import os
import re
from typing import Any, Optional, Tuple

from backend_v2.functions.documents.use_cases import DocumentUseCases

# Module-level lazy singletons for connection reuse across warm invocations
_repository: Any = None
_storage: Any = None
_use_cases: Optional[DocumentUseCases] = None


def get_use_cases() -> DocumentUseCases:
    """Lazily initialize and return the DocumentUseCases orchestrator."""
    global _repository, _storage, _use_cases
    if _use_cases is None:
        from backend_v2.shared.adapters.mongo_document_repository import (
            MongoDocumentRepository,
        )
        from backend_v2.shared.adapters.s3_file_storage import S3FileStorageAdapter

        _repository = MongoDocumentRepository()
        _storage = S3FileStorageAdapter()
        _use_cases = DocumentUseCases(repository=_repository, storage=_storage)
    return _use_cases


def set_use_cases(use_cases: DocumentUseCases) -> None:
    """Override use cases instance (primarily for offline unit testing and mocking)."""
    global _use_cases
    _use_cases = use_cases


_current_origin: str = "*"


def _cors_headers() -> dict[str, str]:
    """Generate standard CORS headers using the caller origin or configured frontend origin."""
    allow_origin = _current_origin or os.getenv("FRONTEND_ORIGIN", "*")
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": allow_origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,Cookie",
        "Access-Control-Allow-Methods": "GET,POST,PATCH,PUT,DELETE,OPTIONS",
    }


def _json_response(status_code: int, body: Any) -> dict[str, Any]:
    """Format a standard JSON response for API Gateway HTTP API v2."""
    return {
        "statusCode": status_code,
        "headers": _cors_headers(),
        "body": json.dumps(body, ensure_ascii=False),
    }


def _error_response(status_code: int, message: str) -> dict[str, Any]:
    """Format a consistent error envelope for client consumption."""
    return _json_response(status_code, {"error": {"message": message}})


def _extract_cookie_token(event: dict[str, Any]) -> Optional[str]:
    """Extract auth_token from HTTP API cookies array or header string."""
    # 1. Check API Gateway v2 event["cookies"] list
    raw_cookies = event.get("cookies") or []
    for item in raw_cookies:
        if isinstance(item, str) and item.startswith("auth_token="):
            return item.split("auth_token=", 1)[1].split(";", 1)[0].strip()

    # 2. Check headers cookie string
    headers = event.get("headers") or {}
    cookie_str = headers.get("cookie") or headers.get("Cookie") or ""
    if cookie_str:
        for part in cookie_str.split(";"):
            part = part.strip()
            if part.startswith("auth_token="):
                return part.split("auth_token=", 1)[1].strip()

    # 3. Check Authorization Bearer fallback
    auth_header = headers.get("authorization") or headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        return auth_header.split("Bearer ", 1)[1].strip()

    return None


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode unverified JWT claims payload safely without external dependencies."""
    parts = token.strip().split(".")
    if len(parts) < 2:
        raise ValueError("Malformed JWT structure")
    payload_b64 = parts[1]
    rem = len(payload_b64) % 4
    if rem > 0:
        payload_b64 += "=" * (4 - rem)
    decoded_bytes = base64.urlsafe_b64decode(payload_b64)
    return json.loads(decoded_bytes.decode("utf-8"))


def _parse_auth(event: dict[str, Any]) -> Tuple[str, bool]:
    """Authenticate request from Cognito JWT cookie/token.

    Returns:
        Tuple[str, bool]: (user_id, is_admin)
    Raises:
        PermissionError: If unauthenticated or token is malformed.
    """
    token = _extract_cookie_token(event)
    if not token:
        raise PermissionError("Authentication is required")

    try:
        claims = _decode_jwt_payload(token)
    except Exception as err:
        raise PermissionError(f"Invalid authentication token: {err}") from err

    # Extract user identifier (username > email > sub)
    user_id = (
        claims.get("cognito:username")
        or claims.get("username")
        or claims.get("email")
        or claims.get("sub")
    )
    if not user_id:
        raise PermissionError("Token contains no valid user identity")

    # Check for admin role via groups or custom attribute
    groups = [str(g).upper() for g in claims.get("cognito:groups", [])]
    role = str(claims.get("custom:role") or claims.get("role") or "").upper()
    is_admin = ("ADMIN" in groups) or (role == "ADMIN")

    return str(user_id), is_admin


def _parse_multipart_body(
    event: dict[str, Any], content_type: str
) -> Tuple[dict[str, str], Optional[dict[str, Any]]]:
    """Parse multipart/form-data from API Gateway payload using standard library."""
    raw_body = event.get("body", "")
    if event.get("isBase64Encoded", False):
        body_bytes = base64.b64decode(raw_body)
    else:
        body_bytes = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body

    msg_bytes = b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body_bytes
    msg = message_from_bytes(msg_bytes, policy=default_policy)

    form_fields: dict[str, str] = {}
    uploaded_file: Optional[dict[str, Any]] = None

    for part in msg.iter_parts():
        disposition = part.get_content_disposition()
        if disposition != "form-data":
            continue

        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()

        if filename:
            file_data = part.get_payload(decode=True)
            uploaded_file = {
                "file_name": filename,
                "file_bytes": file_data or b"",
                "content_type": part.get_content_type() or "application/octet-stream",
            }
        elif name:
            val = part.get_payload(decode=True)
            if isinstance(val, bytes):
                form_fields[name] = val.decode("utf-8", errors="replace")

    return form_fields, uploaded_file


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Main AWS Lambda entrypoint handler for API Gateway HTTP API v2."""
    global _current_origin
    req_headers = event.get("headers") or {}
    _current_origin = req_headers.get("origin") or req_headers.get("Origin") or os.getenv("FRONTEND_ORIGIN", "*")

    http_ctx = event.get("requestContext", {}).get("http", {})
    method = (http_ctx.get("method") or event.get("httpMethod") or "GET").upper()
    raw_path = event.get("rawPath") or http_ctx.get("path") or ""

    # 1. Handle Preflight CORS
    if method == "OPTIONS":
        return {
            "statusCode": 204,
            "headers": _cors_headers(),
            "body": "",
        }

    use_cases = get_use_cases()

    try:
        # 2. Authenticate request
        user_id, is_admin = _parse_auth(event)

        # 3. Route Dispatch
        # GET /api/v1/documents - List documents
        if method == "GET" and (raw_path == "/api/v1/documents" or raw_path == "/api/v1/documents/"):
            documents = use_cases.list_documents(user_id=user_id, is_admin=is_admin)
            return _json_response(200, {
                "documents": [d.to_dict() for d in documents],
                "count": len(documents),
            })

        # POST /api/v1/documents - Register document (Admin only)
        if method == "POST" and (raw_path == "/api/v1/documents" or raw_path == "/api/v1/documents/"):
            if not is_admin:
                return _error_response(403, "Admin privilege required to register documents")

            headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
            content_type = headers.get("content-type", "")

            if not content_type.startswith("multipart/form-data"):
                return _error_response(400, "Content-Type must be multipart/form-data")

            form_fields, file_info = _parse_multipart_body(event, content_type)
            if not file_info:
                return _error_response(400, "Missing uploaded file")

            required = ["title", "document_date", "sender", "receiver"]
            missing = [f for f in required if not form_fields.get(f)]
            if missing:
                return _error_response(400, f"Missing required fields: {', '.join(missing)}")

            doc = use_cases.register_document(
                title=form_fields["title"],
                document_date=form_fields["document_date"],
                sender=form_fields["sender"],
                receiver=form_fields["receiver"],
                file_name=file_info["file_name"],
                file_bytes=file_info["file_bytes"],
                content_type=file_info["content_type"],
                uploaded_by=user_id,
                document_type_id=form_fields.get("document_type_id"),
            )
            return _json_response(201, {
                "message": "Document registered successfully",
                "document": doc.to_dict(),
            })

        # Route matching with document ID
        assign_match = re.match(r"^/api/v1/documents/([^/]+)/assign/?$", raw_path)
        if assign_match and method == "POST":
            doc_id = assign_match.group(1)
            body = json.loads(event.get("body") or "{}")
            recipients = body.get("recipients", [])
            note = body.get("note", "")
            doc = use_cases.assign_document(
                document_id=doc_id,
                recipient_ids=recipients,
                assigned_by=user_id,
                is_admin=is_admin,
                note=note,
            )
            return _json_response(200, {
                "message": "Document assigned successfully",
                "status": doc.status.value,
                "assigned_to": doc.assigned_to,
            })

        status_match = re.match(r"^/api/v1/documents/([^/]+)/status/?$", raw_path)
        if status_match and method == "PATCH":
            doc_id = status_match.group(1)
            body = json.loads(event.get("body") or "{}")
            new_status = body.get("status", "")
            note = body.get("note", "")
            doc = use_cases.update_document_status(
                document_id=doc_id,
                new_status=new_status,
                changed_by=user_id,
                is_admin=is_admin,
                note=note,
            )
            return _json_response(200, {
                "message": "Status updated successfully",
                "status": doc.status.value,
            })

        file_match = re.match(r"^/api/v1/documents/([^/]+)/file/?$", raw_path)
        if file_match and method == "GET":
            doc_id = file_match.group(1)
            file_path, doc = use_cases.get_document_file(
                document_id=doc_id, user_id=user_id, is_admin=is_admin
            )
            with open(file_path, "rb") as f:
                content = f.read()

            params = event.get("queryStringParameters") or {}
            download = str(params.get("download", "")).lower() == "true"
            disposition = (
                f'attachment; filename="{doc.file_name}"'
                if download
                else f'inline; filename="{doc.file_name}"'
            )

            resp_headers = _cors_headers()
            resp_headers["Content-Type"] = doc.file_type or "application/octet-stream"
            resp_headers["Content-Disposition"] = disposition

            return {
                "statusCode": 200,
                "headers": resp_headers,
                "isBase64Encoded": True,
                "body": base64.b64encode(content).decode("ascii"),
            }

        detail_match = re.match(r"^/api/v1/documents/([^/]+)/?$", raw_path)
        if detail_match and method == "GET":
            doc_id = detail_match.group(1)
            doc = use_cases.get_document_details(
                document_id=doc_id, user_id=user_id, is_admin=is_admin
            )
            return _json_response(200, doc.to_dict())

        return _error_response(404, f"Route not found: {method} {raw_path}")

    except PermissionError as err:
        status = 401 if "Authentication is required" in str(err) or "Invalid authentication token" in str(err) else 403
        return _error_response(status, str(err))
    except LookupError as err:
        return _error_response(404, str(err))
    except ValueError as err:
        return _error_response(400, str(err))
    except Exception as err:
        return _error_response(500, f"Internal server error: {str(err)}")
