"""HTTP routes for document registration and retrieval."""

from flask import Blueprint, jsonify, request, send_file, url_for

from backend.services.document_service import DocumentService


documents_bp = Blueprint(
    "documents",
    __name__,
    url_prefix="/api/v1/documents",
)
document_service = DocumentService()


# Convert a Document model into a JSON-safe response for the frontend.
def _document_response(document):
    response = {
        "id": str(document.id),
        "title": document.title,
        "document_date": document.document_date,
        "sender": document.sender,
        "receiver": document.receiver,
        "file_name": document.file_name,
        "file_type": document.file_type,
        "file_size": document.file_size,
        "uploaded_at": document.uploaded_at.isoformat(),
        "uploaded_by": document.uploaded_by,
        "file_url": url_for(
            "documents.get_document_file",
            document_id=str(document.id),
        ),
    }

    # Include the optional document type only when it exists.
    if document.document_type_id is not None:
        response["document_type_id"] = str(document.document_type_id)
    return response


# Return a consistent JSON error response for invalid client input.
def _error(message):
    return jsonify({"error": {"message": message}}), 400


# Register a document and save its uploaded file.
@documents_bp.post("")
def create_document():
    required_fields = ["title", "document_date", "sender", "receiver"]
    missing_fields = [field for field in required_fields if not request.form.get(field)]
    uploaded_file = request.files.get("file")

    if missing_fields:
        return _error("Missing fields: " + ", ".join(missing_fields))
    if uploaded_file is None:
        return _error("Missing file")

    try:
        document = document_service.register_document(
            title=request.form["title"],
            document_date=request.form["document_date"],
            sender=request.form["sender"],
            receiver=request.form["receiver"],
            uploaded_file=uploaded_file,
            document_type_id=request.form.get("document_type_id") or None,
        )
    except ValueError as error:
        return _error(str(error))

    return (
        jsonify(
            {
                "message": "Document registered successfully",
                "document": _document_response(document),
            }
        ),
        201,
    )


# Return metadata for every registered document.
@documents_bp.get("")
def list_documents():
    documents = document_service.get_all_documents()
    return jsonify(
        {
            "documents": [_document_response(document) for document in documents],
            "count": len(documents),
        }
    )


# Return metadata for one registered document.
@documents_bp.get("/<document_id>")
def get_document_metadata(document_id):
    try:
        document = document_service.get_document(document_id)
    except ValueError as error:
        return _error(str(error))

    if document is None:
        return jsonify({"error": {"message": "Document was not found"}}), 404
    return jsonify(_document_response(document))


# Update document metadata and optionally replace its stored file.
@documents_bp.patch("/<document_id>")
def update_document(document_id):
    changes = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    changes = changes or {}
    editable_fields = {
        "title",
        "document_date",
        "sender",
        "receiver",
        "document_type_id",
    }
    unsupported_fields = set(changes) - editable_fields
    if unsupported_fields:
        return _error("Unsupported document fields: " + ", ".join(sorted(unsupported_fields)))

    uploaded_file = request.files.get("file")
    if not changes and uploaded_file is None:
        return _error("No document changes were provided")

    if "document_type_id" in changes and not changes["document_type_id"]:
        changes["document_type_id"] = None

    try:
        document = document_service.update_document(
            document_id,
            changes,
            uploaded_file=uploaded_file,
        )
    except ValueError as error:
        return _error(str(error))

    if document is None:
        return jsonify({"error": {"message": "Document was not found"}}), 404
    return jsonify(
        {
            "message": "Document updated successfully",
            "document": _document_response(document),
        }
    )


# Delete a document and its stored file.
@documents_bp.delete("/<document_id>")
def delete_document(document_id):
    try:
        deleted = document_service.delete_document(document_id)
    except ValueError as error:
        return _error(str(error))

    if not deleted:
        return jsonify({"error": {"message": "Document was not found"}}), 404
    return "", 204


# Return the stored file for viewing or downloading.
@documents_bp.get("/<document_id>/file")
def get_document_file(document_id):
    try:
        document = document_service.get_document(document_id)
        if document is None:
            return jsonify({"error": {"message": "Document was not found"}}), 404
        file_path = document_service.get_document_file(document_id)
    except ValueError as error:
        return _error(str(error))
    except FileNotFoundError:
        return jsonify({"error": {"message": "File was not found"}}), 404

    download = request.args.get("download", "false").lower() == "true"
    return send_file(
        file_path,
        mimetype=document.file_type,
        as_attachment=download,
        download_name=document.file_name,
    )
