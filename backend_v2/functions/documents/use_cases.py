"""Application use cases coordinating Document entities and abstract ports."""

from pathlib import Path
from typing import Any, Optional, Tuple

from backend_v2.shared.domain.document import Document
from backend_v2.shared.domain.document_status import DocumentStatus
from backend_v2.shared.ports.document_repository import DocumentRepositoryPort
from backend_v2.shared.ports.file_storage import FileStoragePort


class DocumentUseCases:
    """Application use-case orchestrator for document lifecycle operations.

    Adheres to Clean Architecture:
    - Composes DocumentRepositoryPort and FileStoragePort via Dependency Injection.
    - Operates purely on Domain entities.
    - Completely decoupled from HTTP frameworks and cloud provider SDKs.
    """

    def __init__(
        self,
        repository: DocumentRepositoryPort,
        storage: FileStoragePort,
    ):
        self._repo = repository
        self._storage = storage

    def register_document(
        self,
        title: str,
        document_date: str,
        sender: str,
        receiver: str,
        file_name: str,
        file_bytes: bytes,
        content_type: str = "application/octet-stream",
        uploaded_by: str = "system",
        document_type_id: Optional[str] = None,
        recipient_ids: Optional[list[str]] = None,
    ) -> Document:
        """Register a new document: uploads file to storage and saves metadata in database.

        Rolls back the uploaded file if database persistence fails.
        """
        # 1. Save binary file using the storage port
        file_meta = self._storage.save_file(
            file_name=file_name,
            file_bytes=file_bytes,
            content_type=content_type,
        )

        try:
            # 2. Build the domain Document entity
            document = Document(
                title=title,
                document_date=document_date,
                sender=sender,
                receiver=receiver,
                file_path=file_meta["file_path"],
                file_name=file_meta["file_name"],
                file_type=file_meta["file_type"],
                file_size=file_meta["file_size"],
                uploaded_by=uploaded_by,
                document_type_id=document_type_id,
            )

            # If initial recipients were specified during registration, assign them
            if recipient_ids:
                document.assign_recipients(
                    recipient_user_ids=recipient_ids,
                    assigned_by=uploaded_by,
                    note="Assigned upon registration",
                )

            # 3. Persist document in repository port
            return self._repo.create(document)

        except Exception:
            # Rollback guard: prevent orphaned files if database fails
            self._storage.delete_file(file_meta["file_path"])
            raise

    def list_documents(
        self,
        user_id: str,
        is_admin: bool = False,
    ) -> list[Document]:
        """List documents based on user roles (Admins see all; Users see assigned)."""
        if is_admin:
            return self._repo.find_all()
        return self._repo.find_by_assigned_user(user_id)

    def get_document_details(
        self,
        document_id: str,
        user_id: str,
        is_admin: bool = False,
    ) -> Document:
        """Fetch document details, enforcing role-based read permissions."""
        document = self._repo.get_by_id(document_id)
        if document is None:
            raise LookupError(f"Document was not found: {document_id}")

        if not document.is_accessible_by(user_id, is_admin=is_admin):
            raise PermissionError("Access denied: You are not assigned to this document")

        return document

    def assign_document(
        self,
        document_id: str,
        recipient_ids: list[str],
        assigned_by: Any,
        is_admin: bool = False,
        note: str = "",
    ) -> Document:
        """Assign or forward document to recipient users (Admin or assigned Department Head)."""
        document = self._repo.get_by_id(document_id)
        if document is None:
            raise LookupError(f"Document was not found: {document_id}")

        # Admin or currently assigned recipient (e.g. Department Head) can forward/assign
        can_assign = is_admin or document.is_accessible_by(assigned_by, is_admin=is_admin)
        if not can_assign:
            raise PermissionError("Permission required to assign or forward this document")

        actor = assigned_by[0] if isinstance(assigned_by, (list, tuple)) else str(assigned_by)
        document.assign_recipients(
            recipient_user_ids=recipient_ids,
            assigned_by=actor,
            note=note,
        )

        update_payload = {
            "status": document.status.value,
            "assigned_to": document.assigned_to,
            "status_history": [e.to_dict() for e in document.status_history],
        }
        self._repo.update(document.id, update_payload)
        return document

    def update_document_status(
        self,
        document_id: str,
        new_status: str,
        changed_by: str,
        is_admin: bool = False,
        note: str = "",
    ) -> Document:
        """Update workflow status (e.g. Lecturer acknowledges receipt)."""
        document = self._repo.get_by_id(document_id)
        if document is None:
            raise LookupError(f"Document was not found: {document_id}")

        # Non-admins can only change status of documents assigned to them
        if not document.is_accessible_by(changed_by, is_admin=is_admin):
            raise PermissionError("Access denied: You cannot update an unassigned document")

        target_status = DocumentStatus(new_status)
        actor = changed_by[0] if isinstance(changed_by, (list, tuple)) else str(changed_by)
        document.transition_status(target_status, changed_by=actor, note=note)

        update_payload = {
            "status": document.status.value,
            "status_history": [e.to_dict() for e in document.status_history],
        }
        self._repo.update(document.id, update_payload)
        return document

    def get_document_file(
        self,
        document_id: str,
        user_id: str,
        is_admin: bool = False,
    ) -> Tuple[Path, Document]:
        """Fetch local file path for streaming, verifying read permissions first."""
        document = self.get_document_details(document_id, user_id, is_admin=is_admin)
        file_path = self._storage.get_file(document.file_path)
        return file_path, document
