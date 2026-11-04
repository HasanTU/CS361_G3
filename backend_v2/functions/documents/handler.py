"""AWS Lambda entrypoint handler for Documents Micro-Lambda."""

import base64
from email import message_from_bytes
from email.policy import default as default_policy
import json
import logging
import os
import re
from typing import Any, Optional, Tuple

from backend_v2.functions.documents.use_cases import DocumentUseCases
from backend_v2.shared.auth import (
    INTERNAL_ERROR_MESSAGE,
    SERVICE_UNAVAILABLE_MESSAGE,
    AuthenticationError,
    AuthServiceUnavailableError,
    authenticate,
    require_admin,
)

logger = logging.getLogger(__name__)

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
        from backend_v2.shared.adapters.cognito_user_directory import (
            CognitoUserDirectory,
        )
        from backend_v2.shared.adapters.s3_file_storage import S3FileStorageAdapter

        _repository = MongoDocumentRepository()
        _storage = S3FileStorageAdapter()
        _use_cases = DocumentUseCases(
            repository=_repository,
            storage=_storage,
            user_directory=CognitoUserDirectory(),
        )
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

    try:
        use_cases = get_use_cases()

        # 2. Authenticate request (verified Cognito ID token)
        caller = authenticate(event)

        # 3. Route Dispatch
        # GET /api/v1/documents - List documents
        if method == "GET" and (raw_path == "/api/v1/documents" or raw_path == "/api/v1/documents/"):
            documents = use_cases.list_documents(caller_sub=caller.sub, is_admin=caller.is_admin)
            return _json_response(200, {
                "documents": [d.to_dict() for d in documents],
                "count": len(documents),
            })

        # POST /api/v1/documents - Register document (Admin only)
        if method == "POST" and (raw_path == "/api/v1/documents" or raw_path == "/api/v1/documents/"):
            require_admin(caller, "Admin privilege required to register documents")

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
                uploaded_by=caller.actor_label,
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
            if not isinstance(recipients, list) or not all(isinstance(r, str) for r in recipients):
                raise ValueError("recipients must be a list of strings")
            doc = use_cases.assign_document(
                document_id=doc_id,
                recipients=recipients,
                caller_sub=caller.sub,
                actor=caller.actor_label,
                is_admin=caller.is_admin,
                note=body.get("note", ""),
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
                caller_sub=caller.sub,
                actor=caller.actor_label,
                is_admin=caller.is_admin,
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
                document_id=doc_id, caller_sub=caller.sub, is_admin=caller.is_admin
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
                document_id=doc_id, caller_sub=caller.sub, is_admin=caller.is_admin
            )
            return _json_response(200, doc.to_dict())

        # PATCH /api/v1/documents/{id} - Edit document
        if detail_match and method == "PATCH":
            doc_id = detail_match.group(1)
            headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
            content_type = headers.get("content-type", "")

            form_fields: dict[str, str] = {}
            file_info: Optional[dict[str, Any]] = None

            if content_type.startswith("multipart/form-data"):
                form_fields, file_info = _parse_multipart_body(event, content_type)
            elif content_type.startswith("application/json"):
                form_fields = json.loads(event.get("body") or "{}")

            doc = use_cases.edit_document(
                document_id=doc_id,
                caller_sub=caller.sub,
                actor=caller.actor_label,
                is_admin=caller.is_admin,
                title=form_fields.get("title"),
                document_date=form_fields.get("document_date"),
                sender=form_fields.get("sender"),
                receiver=form_fields.get("receiver"),
                replacement_file_name=file_info["file_name"] if file_info else None,
                replacement_file_bytes=file_info["file_bytes"] if file_info else None,
                replacement_content_type=file_info["content_type"] if file_info else None,
            )
            return _json_response(200, {
                "message": "Document updated successfully",
                "document": doc.to_dict(),
            })

        # DELETE /api/v1/documents/{id} - Delete document
        if detail_match and method == "DELETE":
            doc_id = detail_match.group(1)
            use_cases.delete_document(
                document_id=doc_id,
                caller_sub=caller.sub,
                is_admin=caller.is_admin,
            )
            return _json_response(200, {
                "message": "Document deleted successfully",
                "id": doc_id,
            })

        return _error_response(404, f"Route not found: {method} {raw_path}")

    except AuthenticationError as err:
        return _error_response(401, str(err))
    except AuthServiceUnavailableError:
        return _error_response(503, SERVICE_UNAVAILABLE_MESSAGE)
    except PermissionError as err:
        return _error_response(403, str(err))
    except LookupError as err:
        return _error_response(404, str(err))
    except ValueError as err:
        return _error_response(400, str(err))
    except Exception:
        logger.exception("Unhandled error in documents handler")
        return _error_response(500, INTERNAL_ERROR_MESSAGE)
