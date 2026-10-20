"""Pure domain entity representing a registered document composed with workflow rules."""

from datetime import datetime, timezone
from typing import Any, Optional

from .document_status import ALLOWED_STATUS_TRANSITIONS, DocumentStatus
from .status_log_entry import StatusLogEntry


class Document:
    """Departmental document domain entity.

    Adheres strictly to Clean Architecture & Composition:
    - Zero third-party dependencies (no MongoDB, PyMongo, BSON, or AWS SDKs).
    - Composes DocumentStatus and StatusLogEntry.
    - Governs document workflow status transitions and recipient assignments.
    """

    def __init__(
        self,
        title: str,
        document_date: str,
        sender: str,
        receiver: str,
        file_path: str,
        file_name: str,
        file_type: str,
        file_size: int,
        uploaded_by: str = "system",
        document_type_id: Optional[str] = None,
        document_id: Optional[str] = None,
        uploaded_at: Optional[datetime] = None,
        status: DocumentStatus = DocumentStatus.RECEIVED,
        assigned_to: Optional[list[str]] = None,
        status_history: Optional[list[StatusLogEntry]] = None,
    ):
        # Attribute validation
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
        if not isinstance(file_size, int) or file_size < 0:
            raise ValueError("Document file size must be a non-negative integer")
        if not isinstance(uploaded_by, str) or not uploaded_by.strip():
            raise ValueError("Document uploader must be a non-empty string")

        # Date validation (YYYY-MM-DD)
        if not isinstance(document_date, str) or not document_date.strip():
            raise ValueError("Document date must be a non-empty string")
        try:
            datetime.strptime(document_date.strip(), "%Y-%m-%d")
        except ValueError as err:
            raise ValueError("Document date must use YYYY-MM-DD format") from err

        self.id = str(document_id).strip() if document_id is not None else None
        self.title = title.strip()
        self.document_date = document_date.strip()
        self.sender = sender.strip()
        self.receiver = receiver.strip()
        self.file_path = file_path.strip()
        self.file_name = file_name.strip()
        self.file_type = file_type.strip()
        self.file_size = file_size
        self.uploaded_by = uploaded_by.strip()
        self.document_type_id = (
            str(document_type_id).strip() if document_type_id is not None else None
        )
        self.uploaded_at = uploaded_at or datetime.now(timezone.utc)

        # Workflow & Assignment composition
        if not isinstance(status, DocumentStatus):
            status = DocumentStatus(status)
        self.status = status
        self.assigned_to = [
            str(uid).strip() for uid in (assigned_to or []) if str(uid).strip()
        ]
        self.status_history: list[StatusLogEntry] = status_history or []

        # If freshly initialized with no history, record initial status entry
        if not self.status_history:
            self.status_history.append(
                StatusLogEntry(
                    status=self.status,
                    changed_by=self.uploaded_by,
                    changed_at=self.uploaded_at,
                    note="Initial document registration",
                )
            )

    def can_transition_to(self, target_status: DocumentStatus) -> bool:
        """Check if transition to target status is valid from current status."""
        if not isinstance(target_status, DocumentStatus):
            target_status = DocumentStatus(target_status)
        return target_status in ALLOWED_STATUS_TRANSITIONS.get(self.status, set())

    def transition_status(
        self,
        new_status: DocumentStatus,
        changed_by: str,
        note: str = "",
    ) -> None:
        """Execute a state transition and append to the audit history."""
        if not isinstance(new_status, DocumentStatus):
            new_status = DocumentStatus(new_status)

        if not self.can_transition_to(new_status):
            raise ValueError(
                f"Invalid workflow transition: Cannot transition from {self.status.value} to {new_status.value}"
            )

        self.status = new_status
        self.status_history.append(
            StatusLogEntry(
                status=new_status,
                changed_by=changed_by,
                note=note,
            )
        )

    def assign_recipients(
        self,
        recipient_user_ids: list[str],
        assigned_by: str,
        note: str = "",
    ) -> None:
        """Assign document to recipient users and transition status to ASSIGNED."""
        cleaned_ids = [str(uid).strip() for uid in recipient_user_ids if str(uid).strip()]
        if not cleaned_ids:
            raise ValueError("Must assign at least one recipient user ID")

        self.assigned_to = cleaned_ids
        target_status = (
            DocumentStatus.FORWARDED
            if self.status != DocumentStatus.RECEIVED
            else DocumentStatus.ASSIGNED
        )
        self.transition_status(target_status, changed_by=assigned_by, note=note)

    def is_accessible_by(self, user_id: str, is_admin: bool = False) -> bool:
        """Determine whether a given user has read permissions for this document."""
        if is_admin:
            return True
        uid = str(user_id).strip().lower()
        assigned_lower = [str(x).strip().lower() for x in self.assigned_to]
        receiver_clean = self.receiver.strip().lower()
        return (uid in assigned_lower) or (uid == receiver_clean)

    def to_dict(self) -> dict[str, Any]:
        """Convert domain entity to a plain dictionary (for API or database)."""
        data: dict[str, Any] = {
            "title": self.title,
            "document_date": self.document_date,
            "sender": self.sender,
            "receiver": self.receiver,
            "file_path": self.file_path,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "uploaded_by": self.uploaded_by,
            "uploaded_at": self.uploaded_at.isoformat(),
            "status": self.status.value,
            "assigned_to": self.assigned_to,
            "status_history": [entry.to_dict() for entry in self.status_history],
        }
        if self.id is not None:
            data["id"] = self.id
        if self.document_type_id is not None:
            data["document_type_id"] = self.document_type_id
        return data
