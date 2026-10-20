"""Infrastructure adapters implementing domain ports."""

from .mongo_document_repository import MongoDocumentRepository
from .s3_file_storage import S3FileStorageAdapter

__all__ = [
    "MongoDocumentRepository",
    "S3FileStorageAdapter",
]
