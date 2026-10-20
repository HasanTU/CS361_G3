"""Infrastructure adapters implementing domain ports."""

from .mongo_document_repository import MongoDocumentRepository

__all__ = ["MongoDocumentRepository"]
