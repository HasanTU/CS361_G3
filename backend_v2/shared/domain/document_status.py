"""Document status enumeration and allowed workflow state transitions."""

from enum import Enum


class DocumentStatus(str, Enum):
    """Lifecycle states for a document in the departmental workflow."""

    RECEIVED = "RECEIVED"          # Admin registered document into the system
    ASSIGNED = "ASSIGNED"          # Admin assigned document to specific recipients
    ACKNOWLEDGED = "ACKNOWLEDGED"  # Recipient opened and confirmed receipt
    IN_PROGRESS = "IN_PROGRESS"    # Recipient is actively processing the document
    COMPLETED = "COMPLETED"        # Action concluded and document archived
    FORWARDED = "FORWARDED"        # Re-assigned / routed to a different recipient


# Deterministic state machine transition rules
ALLOWED_STATUS_TRANSITIONS: dict[DocumentStatus, set[DocumentStatus]] = {
    DocumentStatus.RECEIVED: {DocumentStatus.ASSIGNED},
    DocumentStatus.ASSIGNED: {
        DocumentStatus.ACKNOWLEDGED,
        DocumentStatus.FORWARDED,
    },
    DocumentStatus.FORWARDED: {DocumentStatus.ACKNOWLEDGED},
    DocumentStatus.ACKNOWLEDGED: {
        DocumentStatus.IN_PROGRESS,
        DocumentStatus.COMPLETED,
    },
    DocumentStatus.IN_PROGRESS: {DocumentStatus.COMPLETED},
    DocumentStatus.COMPLETED: set(),
}
