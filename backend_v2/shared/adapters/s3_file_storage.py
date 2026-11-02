"""Amazon S3 object storage adapter implementing FileStoragePort."""

import os
import re
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

from backend_v2.shared.ports.file_storage import FileStoragePort

# Lambda /tmp default is 512 MB; keep usage well below that.
_TMP_MAX_BYTES: int = 200 * 1024 * 1024  # 200 MB soft cap


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

    def _evict_tmp_if_needed(self) -> None:
        """Delete oldest cached files in /tmp/uploads when total size exceeds the soft cap.

        Lambda /tmp is 512 MB by default and accumulates files across warm invocations.
        Evicting LRU files when we breach 200 MB keeps the container healthy long-term.
        """
        files = list(self._temp_dir.glob("*"))
        total = sum(f.stat().st_size for f in files if f.is_file())
        if total <= _TMP_MAX_BYTES:
            return

        # Sort by last-access time (oldest first) and remove until under the cap
        files_with_time = sorted(
            ((f.stat().st_atime, f) for f in files if f.is_file()),
            key=lambda x: x[0],
        )
        for _, f in files_with_time:
            if total <= _TMP_MAX_BYTES:
                break
            size = f.stat().st_size
            try:
                f.unlink()
                total -= size
            except OSError:
                pass  # file may have already been removed; skip silently

    def get_file(self, file_path: str) -> Path:
        """Download an S3 document object into the local Lambda /tmp cache for streaming."""
        self._evict_tmp_if_needed()
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
