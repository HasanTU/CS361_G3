"""Application use cases coordinating Document entities and abstract ports."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

from backend_v2.shared.domain.directory_user import (
    DirectoryUser,
    resolve_recipient,
    resolve_recipients,
)
from backend_v2.shared.domain.document import Document
from backend_v2.shared.domain.document_status import DocumentStatus
from backend_v2.shared.domain.status_log_entry import StatusLogEntry
from backend_v2.shared.ports.document_repository import DocumentRepositoryPort
from backend_v2.shared.ports.file_storage import FileStoragePort
from backend_v2.shared.ports.user_directory import UserDirectoryPort


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
        user_directory: Optional[UserDirectoryPort] = None,
    ):
        self._repo = repository
        self._storage = storage
        self._directory = user_directory

    def _directory_users(self) -> list[DirectoryUser]:
        if self._directory is None:
            raise RuntimeError("User directory is not configured")
        return self._directory.list_users()

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
    ) -> Document:
        """Register a new document: uploads file to storage and saves metadata in database.

        The receiver is resolved to a directory user before the file is saved, so an
        unknown recipient leaves no file behind. Rolls back the uploaded file if
        database persistence fails.
        """
        # 1. Resolve the recipient against the user directory
        recipient = resolve_recipient(receiver, self._directory_users())

        # 2. Save binary file using the storage port
        file_meta = self._storage.save_file(
            file_name=file_name,
            file_bytes=file_bytes,
            content_type=content_type,
        )

        try:
            # 3. Build the domain Document entity
            document = Document(
                title=title,
                document_date=document_date,
                sender=sender,
                receiver=recipient.name,
                file_path=file_meta["file_path"],
                file_name=file_meta["file_name"],
                file_type=file_meta["file_type"],
                file_size=file_meta["file_size"],
                uploaded_by=uploaded_by,
                document_type_id=document_type_id,
            )

            document.assign_recipients(
                [recipient.sub],
                assigned_by=uploaded_by,
                note="Assigned upon registration",
            )

            # 4. Persist document in repository port
            return self._repo.create(document)

        except Exception:
            # Rollback guard: prevent orphaned files if database fails
            self._storage.delete_file(file_meta["file_path"])
            raise

    def list_documents(
        self,
        caller_sub: str,
        is_admin: bool = False,
    ) -> list[Document]:
        """List documents based on user roles (Admins see all; Users see assigned)."""
        if is_admin:
            return self._repo.find_all()
        return self._repo.find_by_assigned_user(caller_sub)

    def get_document_details(
        self,
        document_id: str,
        caller_sub: str,
        is_admin: bool = False,
    ) -> Document:
        """Fetch document details, enforcing role-based read permissions."""
        document = self._repo.get_by_id(document_id)
        if document is None:
            raise LookupError(f"Document was not found: {document_id}")

        if not document.is_accessible_by(caller_sub, is_admin=is_admin):
            raise PermissionError("Access denied: You are not assigned to this document")

        return document

    def assign_document(
        self,
        document_id: str,
        recipients: list[str],
        caller_sub: str,
        actor: str,
        is_admin: bool = False,
        note: str = "",
    ) -> Document:
        """Assign or forward document to recipient users (Admin or assigned Department Head)."""
        document = self._repo.get_by_id(document_id)
        if document is None:
            raise LookupError(f"Document was not found: {document_id}")

        # Admin or currently assigned recipient (e.g. Department Head) can forward/assign
        if not (is_admin or document.is_accessible_by(caller_sub)):
            raise PermissionError("Permission required to assign or forward this document")

        # Resolved after the permission check so outsiders cannot probe the directory
        users = resolve_recipients(recipients, self._directory_users())
        document.assign_recipients(
            [u.sub for u in users],
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
        caller_sub: str,
        actor: str,
        is_admin: bool = False,
        note: str = "",
    ) -> Document:
        """Update workflow status (e.g. Lecturer acknowledges receipt)."""
        document = self._repo.get_by_id(document_id)
        if document is None:
            raise LookupError(f"Document was not found: {document_id}")

        # Non-admins can only change status of documents assigned to them
        if not document.is_accessible_by(caller_sub, is_admin=is_admin):
            raise PermissionError("Access denied: You cannot update an unassigned document")

        target_status = DocumentStatus(new_status)
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
        caller_sub: str,
        is_admin: bool = False,
    ) -> Tuple[Path, Document]:
        """Fetch local file path for streaming, verifying read permissions first."""
        document = self.get_document_details(document_id, caller_sub, is_admin=is_admin)
        file_path = self._storage.get_file(document.file_path)
        return file_path, document

    def edit_document(
        self,
        document_id: str,
        caller_sub: str,
        actor: str,
        is_admin: bool,
        title: Optional[str] = None,
        document_date: Optional[str] = None,
        sender: Optional[str] = None,
        receiver: Optional[str] = None,
        replacement_file_name: Optional[str] = None,
        replacement_file_bytes: Optional[bytes] = None,
        replacement_content_type: Optional[str] = None,
    ) -> Document:
        """Edit document metadata and optionally replace its stored file (Admin only)."""
        document = self.get_document_details(document_id, caller_sub, is_admin=is_admin)
        if not is_admin:
            raise PermissionError("Admin privilege required to edit documents")

        update_payload: dict[str, Any] = {}
        if title and title.strip():
            document.title = title.strip()
            update_payload["title"] = document.title

        if document_date and document_date.strip():
            datetime.strptime(document_date.strip(), "%Y-%m-%d")
            document.document_date = document_date.strip()
            update_payload["document_date"] = document.document_date

        if sender and sender.strip():
            document.sender = sender.strip()
            update_payload["sender"] = document.sender

        if receiver and receiver.strip() and receiver.strip() != document.receiver:
            users = self._directory_users()
            resolved_sub = resolve_recipient(receiver, users)
            document.receiver = receiver.strip()
            document.assigned_to = [resolved_sub]
            update_payload["receiver"] = document.receiver
            update_payload["assigned_to"] = document.assigned_to

        if replacement_file_name and replacement_file_bytes:
            stored_meta = self._storage.save_file(
                file_name=replacement_file_name,
                file_bytes=replacement_file_bytes,
                content_type=replacement_content_type or "application/octet-stream",
            )
            old_path = document.file_path
            try:
                self._storage.delete_file(old_path)
            except Exception:
                pass

            document.file_path = stored_meta["file_path"]
            document.file_name = stored_meta["file_name"]
            document.file_type = stored_meta["file_type"]
            document.file_size = stored_meta["file_size"]

            update_payload["file_path"] = document.file_path
            update_payload["file_name"] = document.file_name
            update_payload["file_type"] = document.file_type
            update_payload["file_size"] = document.file_size

        entry = StatusLogEntry(
            status=document.status,
            changed_by=actor,
            changed_at=datetime.now(timezone.utc),
            note="Document details updated",
        )
        document.status_history.append(entry)
        update_payload["status_history"] = [e.to_dict() for e in document.status_history]

        self._repo.update(document.id, update_payload)
        return document

    def delete_document(
        self,
        document_id: str,
        caller_sub: str,
        is_admin: bool,
    ) -> None:
        """Permanently delete a document record and its binary file from storage (Admin only)."""
        document = self.get_document_details(document_id, caller_sub, is_admin=is_admin)
        if not is_admin:
            raise PermissionError("Admin privilege required to delete documents")

        try:
            self._storage.delete_file(document.file_path)
        except Exception:
            pass

        self._repo.delete(document.id)
