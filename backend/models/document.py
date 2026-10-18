"""Model for registered documents."""

from datetime import datetime, timezone

from bson import ObjectId

from backend.models.database import db


class Document:
    """Represent a registered document stored in MongoDB."""

    # Reuse one collection handle for all registered document operations.
    collection = db["documents"]

    # Create a document model and validate the fields required by the schema.
    def __init__(
        self,
        title,
        document_date,
        sender,
        receiver,
        file_path,
        file_name,
        file_type,
        file_size,
        uploaded_by="system",
        document_type_id=None,
        uploaded_at=None,
        document_id=None,
    ):
        # Text fields must contain useful values after trimming whitespace.
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Document title must be a non-empty string")
        if not isinstance(sender, str) or not sender.strip():
            raise ValueError("Document sender must be a non-empty string")
        if not isinstance(receiver, str) or not receiver.strip():
            raise ValueError("Document receiver must be a non-empty string")
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError("Document file path must be a non-empty string")
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError("Document file name must be a non-empty string")
        if not isinstance(file_type, str) or not file_type.strip():
            raise ValueError("Document file type must be a non-empty string")

        # File sizes should never be negative and must be stored as integers.
        if not isinstance(file_size, int) or file_size < 0:
            raise ValueError("Document file size must be a non-negative integer")
        if not isinstance(uploaded_by, str) or not uploaded_by.strip():
            raise ValueError("Document uploader must be a non-empty string")

        # Store the frontend date field as date-only ISO text because it does
        # not contain a time or timezone.
        if not isinstance(document_date, str) or not document_date.strip():
            raise ValueError("Document date must be a non-empty string")
        try:
            datetime.strptime(document_date, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError("Document date must use YYYY-MM-DD format") from error

        # MongoDB uses _id for the document's identity; it is optional before
        # insertion because MongoDB can generate it automatically.
        self.id = document_id

        # References between collections are stored as ObjectId values rather
        # than plain strings. The field is optional until the UI provides a
        # document type selector.
        self.document_type_id = (
            self._to_object_id(document_type_id)
            if document_type_id is not None
            else None
        )
        self.title = title.strip()
        self.document_date = document_date.strip()
        self.sender = sender.strip()
        self.receiver = receiver.strip()
        self.file_path = file_path.strip()
        self.file_name = file_name.strip()
        self.file_type = file_type.strip()
        self.file_size = file_size

        # Use UTC so timestamps remain consistent across local and Docker runs.
        self.uploaded_at = uploaded_at or datetime.now(timezone.utc)
        self.uploaded_by = uploaded_by.strip()

    # Normalize an ObjectId value and provide a clear model-level error for an
    # invalid reference.
    @staticmethod
    def _to_object_id(value, field_name="document_type_id"):
        if isinstance(value, ObjectId):
            return value
        try:
            return ObjectId(value)
        except Exception as error:
            raise ValueError(f"{field_name} must be a valid ObjectId") from error

    # Convert the Python model into the shape expected by MongoDB.
    def to_mongo(self):
        """Return the model as a MongoDB document."""
        document = {
            "title": self.title,
            "document_date": self.document_date,
            "sender": self.sender,
            "receiver": self.receiver,
            "file_path": self.file_path,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "uploaded_at": self.uploaded_at,
            "uploaded_by": self.uploaded_by,
        }

        # Keep the relation optional while the frontend has no document type
        # input field.
        if self.document_type_id is not None:
            document["document_type_id"] = self.document_type_id

        # Include _id only when the model already represents a stored record.
        if self.id is not None:
            document["_id"] = self.id
        return document

    # Insert this new model into MongoDB and keep the generated _id.
    def create(self):
        """Insert the model and return this model with its MongoDB id."""
        if self.id is not None:
            raise ValueError("Cannot create a document that already has an id")

        result = self.collection.insert_one(self.to_mongo())
        self.id = result.inserted_id
        return self

    # Find one document by its MongoDB id.
    @classmethod
    def find_by_id(cls, document_id):
        """Return a matching model or None when it does not exist."""
        object_id = cls._to_object_id(document_id, "document_id")
        document = cls.collection.find_one({"_id": object_id})
        return cls.from_mongo(document) if document else None

    # Read all registered documents from the collection.
    @classmethod
    def find_all(cls):
        """Return all registered document models."""
        return [cls.from_mongo(document) for document in cls.collection.find()]

    # Validate and update one or more editable fields of an existing document.
    def update(self, **changes):
        """Update this model and return whether its record was found."""
        if self.id is None:
            raise ValueError("Cannot update a document without an id")

        editable_fields = {
            "document_type_id",
            "title",
            "document_date",
            "sender",
            "receiver",
            "file_path",
            "file_name",
            "file_type",
            "file_size",
            "uploaded_at",
            "uploaded_by",
        }
        unknown_fields = set(changes) - editable_fields
        if unknown_fields:
            raise ValueError(
                "Unsupported document fields: "
                + ", ".join(sorted(unknown_fields))
            )

        # Build a complete candidate model so every update uses the same
        # validation rules as a newly created document.
        values = {
            "document_type_id": self.document_type_id,
            "title": self.title,
            "document_date": self.document_date,
            "sender": self.sender,
            "receiver": self.receiver,
            "file_path": self.file_path,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "uploaded_at": self.uploaded_at,
            "uploaded_by": self.uploaded_by,
        }
        values.update(changes)
        updated_model = Document(document_id=self.id, **values)
        mongo_document = updated_model.to_mongo()
        mongo_document.pop("_id")

        update_operations = {"$set": mongo_document}
        if updated_model.document_type_id is None:
            update_operations["$unset"] = {"document_type_id": ""}

        result = self.collection.update_one(
            {"_id": self.id},
            update_operations,
        )

        if result.matched_count:
            # Keep this Python object synchronized with the saved values.
            self.__dict__.update(updated_model.__dict__)
            return True
        return False

    # Delete this document from MongoDB.
    def delete(self):
        """Delete this model and return whether a record was removed."""
        if self.id is None:
            raise ValueError("Cannot delete a document without an id")

        result = self.collection.delete_one({"_id": self.id})
        return result.deleted_count == 1

    # Convert a MongoDB result back into a Python model instance.
    @classmethod
    def from_mongo(cls, document):
        """Build a model from a MongoDB document."""
        return cls(
            title=document["title"],
            document_date=document["document_date"],
            sender=document["sender"],
            receiver=document["receiver"],
            file_path=document["file_path"],
            file_name=document["file_name"],
            file_type=document["file_type"],
            file_size=document["file_size"],
            uploaded_at=document.get("uploaded_at"),
            uploaded_by=document.get("uploaded_by", "system"),
            document_type_id=document.get("document_type_id"),
            document_id=document.get("_id"),
        )
