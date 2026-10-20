"""MongoDB Atlas adapter implementing DocumentRepositoryPort."""

import os
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

        return Document(
            document_id=str(record["_id"]),
            title=record.get("title", ""),
            document_date=record.get("document_date", ""),
            sender=record.get("sender", ""),
            receiver=record.get("receiver", ""),
            file_path=record.get("file_path", ""),
            file_name=record.get("file_name", ""),
            file_type=record.get("file_type", ""),
            file_size=record.get("file_size", 0),
            uploaded_by=record.get("uploaded_by", "system"),
            uploaded_at=record.get("uploaded_at"),
            document_type_id=(
                str(record["document_type_id"])
                if record.get("document_type_id")
                else None
            ),
            status=DocumentStatus(
                record.get("status", DocumentStatus.RECEIVED.value)
            ),
            assigned_to=record.get("assigned_to") or [],
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
        return self._to_domain(record) if record else None

    def find_all(
        self, filter_criteria: Optional[dict[str, Any]] = None
    ) -> list[Document]:
        """Retrieve all documents matching the optional filter criteria."""
        query = dict(filter_criteria or {})
        cursor = self.collection.find(query)
        return [self._to_domain(record) for record in cursor]

    def find_by_assigned_user(self, user_id: str) -> list[Document]:
        """Retrieve all documents assigned to a given user ID or receiver."""
        cleaned_uid = str(user_id).strip()
        query = {
            "$or": [
                {"assigned_to": cleaned_uid},
                {"receiver": cleaned_uid},
                {"assigned_to": {"$regex": f"^{cleaned_uid}$", "$options": "i"}},
                {"receiver": {"$regex": f"^{cleaned_uid}$", "$options": "i"}},
            ]
        }
        cursor = self.collection.find(query)
        return [self._to_domain(record) for record in cursor]

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
