"""Abstract Port defining the persistence contract for Document entities."""

from abc import ABC, abstractmethod
from typing import Any, Optional

from backend_v2.shared.domain.document import Document


class DocumentRepositoryPort(ABC):
    """Outbound port for document data storage and retrieval.

    Any persistence mechanism (MongoDB, DynamoDB, PostgreSQL, or an in-memory
    mock for testing) must implement this interface.
    """

    @abstractmethod
    def get_by_id(self, document_id: str) -> Optional[Document]:
        """Fetch a single document by its unique identifier.

        Returns None if not found.
        """
        pass

    @abstractmethod
    def find_all(
        self, filter_criteria: Optional[dict[str, Any]] = None
    ) -> list[Document]:
        """Retrieve all documents matching the optional filter criteria."""
        pass

    @abstractmethod
    def find_by_assigned_user(self, user_id: str) -> list[Document]:
        """Retrieve all documents where the given user_id is in assigned_to."""
        pass

    @abstractmethod
    def create(self, document: Document) -> Document:
        """Persist a new document entity.

        Returns the document with its generated identifier populated.
        """
        pass

    @abstractmethod
    def update(self, document_id: str, changes: dict[str, Any]) -> bool:
        """Update fields of an existing document.

        Returns True if the document was found and updated, False otherwise.
        """
        pass

    @abstractmethod
    def delete(self, document_id: str) -> bool:
        """Remove a document record from persistence.

        Returns True if a document was deleted, False otherwise.
        """
        pass
