"""Load mock documents after their real files have been placed in uploads/."""

import json
import mimetypes
from pathlib import Path

from backend.models.database import db


DOCUMENTS_FILE = Path(__file__).resolve().parents[1] / "data" / "mock_documents.json"
PROJECT_ROOT = DOCUMENTS_FILE.parents[1]


def seed_documents():
    """Upsert mock documents and derive file metadata from each real file."""
    documents = json.loads(DOCUMENTS_FILE.read_text(encoding="utf-8"))
    collection = db["documents"]

    resolved_files = []
    for document in documents:
        file_path = Path(document["file_path"])
        if not file_path.is_absolute():
            file_path = PROJECT_ROOT / file_path
        if not file_path.is_file():
            print(f"Skipping mock document seed; missing file: {file_path}")
            return False
        resolved_files.append(file_path)

    for document, file_path in zip(documents, resolved_files):
        required_fields = ["title", "document_date", "sender", "receiver", "file_path", "file_name", "uploaded_by"]
        missing_fields = [field for field in required_fields if not document.get(field)]
        if missing_fields:
            raise ValueError(f"Missing fields for mock document: {', '.join(missing_fields)}")

        document_to_store = {
            **document,
            "file_path": str(file_path.resolve()),
            "file_size": file_path.stat().st_size,
            "file_type": mimetypes.guess_type(file_path.name)[0] or "application/octet-stream",
        }
        collection.update_one(
            {
                "title": document["title"],
                "document_date": document["document_date"],
                "receiver": document["receiver"],
            },
            {"$set": document_to_store},
            upsert=True,
        )
    return True


if __name__ == "__main__":
    print("Mock documents seeded" if seed_documents() else "Mock document seed skipped")
