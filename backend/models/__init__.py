"""MongoDB models."""

#provide cleaner import for other class
from backend.models.document import Document
from backend.models.document_type import DocumentType
from backend.models.user import User

__all__ = ["Document", "DocumentType", "User"]
