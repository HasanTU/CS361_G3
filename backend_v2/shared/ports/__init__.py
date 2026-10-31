"""Abstract Ports (interfaces) for Hexagonal Architecture."""

from .document_repository import DocumentRepositoryPort
from .file_storage import FileStoragePort
from .user_directory import UserDirectoryPort

__all__ = [
    "DocumentRepositoryPort",
    "FileStoragePort",
    "UserDirectoryPort",
]
