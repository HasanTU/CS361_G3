"""MongoDB Atlas adapter implementing DocumentRepositoryPort."""

import os
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import MongoClient
from pymongo.collection import Collection

from backend_v2.shared.domain.document import Document
from backend_v2.shared.domain.document_status import DocumentStatus
from backend_v2.shared.domain.status_log_entry import StatusLogEntry
from backend_v2.shared.ports.document_repository import DocumentRepositoryPort


class MongoDocumentRepository(DocumentRepositoryPort):
    """Concrete persistence adapter connecting Document entities to MongoDB Atlas."""

    def __init__(
        self,
        mongo_uri: Optional[str] = None,
        database_name: Optional[str] = None,
    ):
        self._uri = mongo_uri or os.getenv(
            "MONGO_URI", "mongodb://localhost:27017/e_mailbox"
        )
        self._database_name = database_name
        self._client: Optional[MongoClient] = None
        self._collection: Optional[Collection] = None

    @property
    def collection(self) -> Collection:
        """Lazily initialize and return the MongoDB collection handle."""
        if self._collection is None:
            self._client = MongoClient(self._uri)
            # Default database name is parsed directly from the URI (e.g., /e_mailbox)
            db = (
                self._client[self._database_name]
                if self._database_name
                else self._client.get_default_database()
            )
            self._collection = db["documents"]
        return self._collection

    @staticmethod
    def _parse_object_id(id_str: str) -> Optional[ObjectId]:
        """Safely convert string to BSON ObjectId, returning None if invalid."""
        try:
            return ObjectId(str(id_str).strip())
        except Exception:
            return None

    def _to_domain(self, record: dict[str, Any]) -> Document:
        """Translate a raw MongoDB BSON document into a pure Document domain entity."""
        raw_history = record.get("status_history") or []
        history_entries = []
        for h in raw_history:
            try:
                history_entries.append(StatusLogEntry.from_dict(h))
            except Exception:
                continue

        # Robust date parsing (handles datetime, ISO strings with timestamps, and formats)
        raw_date = record.get("document_date")
        if hasattr(raw_date, "strftime"):
            doc_date = raw_date.strftime("%Y-%m-%d")
        elif isinstance(raw_date, str) and raw_date.strip():
            first_part = raw_date.strip().split("T")[0].split(" ")[0].replace("/", "-")
            try:
                datetime.strptime(first_part, "%Y-%m-%d")
                doc_date = first_part
            except Exception:
                doc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        else:
            doc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Safe attribute defaults to prevent Document.__init__ ValueError
        title = str(record.get("title") or "Untitled Document").strip() or "Untitled Document"
        sender = str(record.get("sender") or "System").strip() or "System"
        receiver = str(record.get("receiver") or "General").strip() or "General"
        file_path = str(record.get("file_path") or f"documents/{record.get('_id')}.pdf").strip()
        file_name = str(record.get("file_name") or f"{title}.pdf").strip()
        file_type = str(record.get("file_type") or "application/pdf").strip() or "application/pdf"
        file_size = int(record.get("file_size") or 0)
        uploaded_by = str(record.get("uploaded_by") or "system").strip() or "system"

        raw_status = str(record.get("status", DocumentStatus.RECEIVED.value)).upper()
        try:
            status = DocumentStatus(raw_status)
        except Exception:
            status = DocumentStatus.RECEIVED

        raw_assigned = record.get("assigned_to")
        if isinstance(raw_assigned, list):
            assigned_to = [str(x).strip() for x in raw_assigned if x and str(x).strip()]
        elif isinstance(raw_assigned, str) and raw_assigned.strip():
            assigned_to = [raw_assigned.strip()]
        else:
            assigned_to = []

        raw_uploaded = record.get("uploaded_at")
        if isinstance(raw_uploaded, str):
            try:
                uploaded_at = datetime.fromisoformat(raw_uploaded)
            except Exception:
                uploaded_at = datetime.now(timezone.utc)
        elif hasattr(raw_uploaded, "isoformat"):
            uploaded_at = raw_uploaded
        else:
            uploaded_at = datetime.now(timezone.utc)

        return Document(
            document_id=str(record["_id"]),
            title=title,
            document_date=doc_date,
            sender=sender,
            receiver=receiver,
            file_path=file_path,
            file_name=file_name,
            file_type=file_type,
            file_size=file_size,
            uploaded_by=uploaded_by,
            uploaded_at=uploaded_at,
            document_type_id=(
                str(record["document_type_id"])
                if record.get("document_type_id")
                else None
            ),
            status=status,
            assigned_to=assigned_to,
            status_history=history_entries,
        )

    def _to_mongo(self, document: Document) -> dict[str, Any]:
        """Translate a pure Document domain entity into a MongoDB BSON dictionary."""
        mongo_data = document.to_dict()

        # Remove string ID from insert payload; MongoDB generates BSON _id
        mongo_data.pop("id", None)

        # Convert document_type_id to BSON ObjectId if present
        if document.document_type_id:
            oid = self._parse_object_id(document.document_type_id)
            if oid:
                mongo_data["document_type_id"] = oid

        return mongo_data

    def get_by_id(self, document_id: str) -> Optional[Document]:
        """Fetch a single document by its MongoDB ObjectId."""
        oid = self._parse_object_id(document_id)
        if oid is None:
            return None

        record = self.collection.find_one({"_id": oid})
        if not record:
            return None
        try:
            return self._to_domain(record)
        except Exception as err:
            print(f"[REPO_WARN] Skipped unparseable record {document_id}: {err}")
            return None

    def find_all(
        self, filter_criteria: Optional[dict[str, Any]] = None
    ) -> list[Document]:
        """Retrieve all documents matching the optional filter criteria."""
        query = dict(filter_criteria or {})
        cursor = self.collection.find(query)
        docs = []
        for record in cursor:
            try:
                docs.append(self._to_domain(record))
            except Exception as err:
                print(f"[REPO_WARN] Skipped malformed record {record.get('_id')}: {err}")
        return docs

    def find_by_assigned_user(self, user_id: Any) -> list[Document]:
        """Retrieve all documents assigned to a given user ID, email, or name."""
        import re

        if isinstance(user_id, (list, tuple, set)):
            candidates = [str(x).strip() for x in user_id if x and str(x).strip()]
        else:
            candidates = [str(user_id).strip()] if user_id else []

        if not candidates:
            return []

        or_conditions: list[dict[str, Any]] = []
        for cand in candidates:
            escaped = re.escape(cand)
            or_conditions.extend(
                [
                    {"assigned_to": cand},
                    {"receiver": cand},
                    {"assigned_to": {"$regex": f"^{escaped}$", "$options": "i"}},
                    {"receiver": {"$regex": f"^{escaped}$", "$options": "i"}},
                ]
            )

        query = {"$or": or_conditions} if or_conditions else {}
        cursor = self.collection.find(query)
        docs = []
        for record in cursor:
            try:
                docs.append(self._to_domain(record))
            except Exception as err:
                print(f"[REPO_WARN] Skipped malformed record {record.get('_id')}: {err}")
        return docs

    def create(self, document: Document) -> Document:
        """Insert a new Document entity into MongoDB and populate its generated ID."""
        mongo_payload = self._to_mongo(document)
        result = self.collection.insert_one(mongo_payload)
        document.id = str(result.inserted_id)
        return document

    def update(self, document_id: str, changes: dict[str, Any]) -> bool:
        """Update specific fields of an existing document in MongoDB."""
        oid = self._parse_object_id(document_id)
        if oid is None:
            return False

        update_payload = dict(changes)
        update_payload.pop("_id", None)
        update_payload.pop("id", None)

        result = self.collection.update_one(
            {"_id": oid},
            {"$set": update_payload},
        )
        return result.matched_count > 0

    def delete(self, document_id: str) -> bool:
        """Remove a document record from MongoDB."""
        oid = self._parse_object_id(document_id)
        if oid is None:
            return False

        result = self.collection.delete_one({"_id": oid})
        return result.deleted_count > 0
