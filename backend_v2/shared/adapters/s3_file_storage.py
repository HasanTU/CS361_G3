"""Amazon S3 object storage adapter implementing FileStoragePort."""

import os
import re
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

from backend_v2.shared.ports.file_storage import FileStoragePort


class S3FileStorageAdapter(FileStoragePort):
    """Concrete storage adapter persisting document files to Amazon S3."""

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        region_name: Optional[str] = None,
        temp_dir: Optional[str] = None,
    ):
        self._bucket_name = bucket_name or os.getenv(
            "S3_BUCKET_NAME", "cs361-g3-documents"
        )
        self._region_name = region_name or os.getenv("AWS_REGION", "us-east-1")
        self._s3_client = None
        self._temp_dir = Path(temp_dir or "/tmp/uploads")
        self._temp_dir.mkdir(parents=True, exist_ok=True)

    @property
    def s3_client(self):
        """Lazily initialize and return the boto3 S3 client handle."""
        if self._s3_client is None:
            self._s3_client = boto3.client("s3", region_name=self._region_name)
        return self._s3_client

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Sanitize filename to prevent directory traversal and special character exploits."""
        # Normalize Windows backslashes to forward slashes before extracting basename
        normalized = str(filename).replace("\\", "/")
        name = Path(normalized).name
        # Keep alphanumeric, periods, dashes, and underscores only
        cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
        cleaned = cleaned.strip("._")
        if not cleaned:
            return f"document_{uuid4().hex[:8]}.bin"
        return cleaned

    def save_file(
        self,
        file_name: str,
        file_bytes: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        """Upload file bytes to the Amazon S3 bucket with a unique UUID key."""
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError("Uploaded file must have a non-empty filename")
        if not isinstance(file_bytes, (bytes, bytearray)):
            raise ValueError("File bytes must be provided as bytes or bytearray")

        safe_name = self._sanitize_filename(file_name)
        s3_key = f"uploads/{uuid4().hex}_{safe_name}"

        self.s3_client.put_object(
            Bucket=self._bucket_name,
            Key=s3_key,
            Body=file_bytes,
            ContentType=content_type,
        )

        return {
            "file_path": s3_key,
            "file_name": safe_name,
            "file_type": content_type,
            "file_size": len(file_bytes),
        }

    def get_file(self, file_path: str) -> Path:
        """Download an S3 document object into the local Lambda /tmp cache for streaming."""
        s3_key = str(file_path).lstrip("/")
        if "uploads/" in s3_key:
            s3_key = s3_key[s3_key.find("uploads/"):]

        local_path = self._temp_dir / Path(s3_key).name
        if not local_path.exists():
            try:
                self.s3_client.download_file(self._bucket_name, s3_key, str(local_path))
            except ClientError as err:
                raise FileNotFoundError(f"Stored S3 file not found: {s3_key}") from err

        return local_path

    def delete_file(self, file_path: str) -> bool:
        """Remove a document object from Amazon S3 and clear any local cached copy."""
        s3_key = str(file_path).lstrip("/")
        if "uploads/" in s3_key:
            s3_key = s3_key[s3_key.find("uploads/"):]

        try:
            self.s3_client.delete_object(Bucket=self._bucket_name, Key=s3_key)
            local_path = self._temp_dir / Path(s3_key).name
            if local_path.exists():
                local_path.unlink()
            return True
        except ClientError:
            return False
