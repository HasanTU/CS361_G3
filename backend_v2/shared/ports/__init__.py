"""Abstract Ports (interfaces) for Hexagonal Architecture."""

from .document_repository import DocumentRepositoryPort
from .file_storage import FileStoragePort

__all__ = [
    "DocumentRepositoryPort",
    "FileStoragePort",
]
