"""Abstract Port defining the binary object storage contract."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class FileStoragePort(ABC):
    """Outbound port for binary file storage operations.

    Any storage mechanism (Amazon S3, Google Cloud Storage, local filesystem,
    or in-memory buffer) must implement this interface.
    """

    @abstractmethod
    def save_file(
        self,
        file_name: str,
        file_bytes: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        """Store raw file bytes and return storage metadata.

        Expected return dictionary shape:
        {
            "file_path": str,      # Unique storage key or path
            "file_name": str,      # Original sanitized filename
            "file_type": str,      # MIME type
            "file_size": int       # Total size in bytes
        }
        """
        pass

    @abstractmethod
    def get_file(self, file_path: str) -> Path:
        """Retrieve stored file path for reading or streaming.

        Raises FileNotFoundError if the file does not exist.
        """
        pass

    @abstractmethod
    def delete_file(self, file_path: str) -> bool:
        """Delete stored file.

        Returns True if the file was deleted or did not exist, False on failure.
        """
        pass
