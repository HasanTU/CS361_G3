"""Audit trail log entry recording document workflow state transitions."""

from datetime import datetime, timezone
from typing import Any, Optional

from .document_status import DocumentStatus


class StatusLogEntry:
    """Audit trail record of a single workflow state change."""

    def __init__(
        self,
        status: DocumentStatus,
        changed_by: str,
        changed_at: Optional[datetime] = None,
        note: str = "",
    ):
        if not isinstance(status, DocumentStatus):
            status = DocumentStatus(status)
        if not isinstance(changed_by, str) or not changed_by.strip():
            raise ValueError("Status changer ID must be a non-empty string")

        self.status = status
        self.changed_by = changed_by.strip()
        self.changed_at = changed_at or datetime.now(timezone.utc)
        self.note = (note or "").strip()

    def to_dict(self) -> dict[str, Any]:
        """Convert entry into serializable dictionary."""
        return {
            "status": self.status.value,
            "changed_by": self.changed_by,
            "changed_at": self.changed_at.isoformat(),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StatusLogEntry":
        """Reconstruct audit log entry from dictionary."""
        changed_at = data.get("changed_at")
        if isinstance(changed_at, str):
            changed_at = datetime.fromisoformat(changed_at)
        return cls(
            status=DocumentStatus(data["status"]),
            changed_by=data["changed_by"],
            changed_at=changed_at,
            note=data.get("note", ""),
        )
