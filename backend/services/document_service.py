"""Business operations for registered documents."""

from backend.models import Document, DocumentType
from backend.services.file_storage_service import FileStorageService


class DocumentService:
    """Coordinate document metadata and uploaded file operations."""

    # Create the service and allow a storage implementation to be supplied in
    # tests or by a future application configuration.
    def __init__(self, file_storage_service=None):
        self.file_storage_service = file_storage_service or FileStorageService()

    # Save the file and its MongoDB metadata as one document-registration flow.
    def register_document(
        self,
        title,
        document_date,
        sender,
        receiver,
        uploaded_file,
        uploaded_by="system",
        document_type_id=None,
    ):
        """Store an uploaded file and return its registered Document model."""
        # Validate the optional document type only when the caller supplies it.
        if (
            document_type_id is not None
            and DocumentType.find_by_id(document_type_id) is None
        ):
            raise ValueError("Document type was not found")

        file_metadata = self.file_storage_service.save_file(uploaded_file)
        try:
            document = Document(
                title=title,
                document_date=document_date,
                sender=sender,
                receiver=receiver,
                file_path=file_metadata["file_path"],
                file_name=file_metadata["file_name"],
                file_type=file_metadata["file_type"],
                file_size=file_metadata["file_size"],
                uploaded_by=uploaded_by,
                document_type_id=document_type_id,
            )
            return document.create()
        except Exception:
            # Prevent an orphaned file when MongoDB registration fails.
            self.file_storage_service.delete_file(file_metadata["file_path"])
            raise

    # Retrieve one registered document by its MongoDB id.
    def get_document(self, document_id):
        """Return the document or None when it does not exist."""
        return Document.find_by_id(document_id)

    # Retrieve all registered documents.
    def get_all_documents(self):
        """Return all registered document models."""
        return Document.find_all()

    # Validate and update metadata for an existing registered document.
    def update_document(self, document_id, changes, uploaded_file=None):
        """Update document metadata and optionally replace its file."""
        document = self.get_document(document_id)
        if document is None:
            return None

        if "document_type_id" in changes:
            document_type_id = changes["document_type_id"]
            if (
                document_type_id is not None
                and DocumentType.find_by_id(document_type_id) is None
            ):
                raise ValueError("Document type was not found")

        old_file_path = document.file_path
        new_file_path = None
        if uploaded_file is not None:
            file_metadata = self.file_storage_service.save_file(uploaded_file)
            new_file_path = file_metadata["file_path"]
            changes = {
                **changes,
                "file_path": new_file_path,
                "file_name": file_metadata["file_name"],
                "file_type": file_metadata["file_type"],
                "file_size": file_metadata["file_size"],
            }

        try:
            if not document.update(**changes):
                if new_file_path is not None:
                    self.file_storage_service.delete_file(new_file_path)
                return None
        except Exception:
            if new_file_path is not None:
                self.file_storage_service.delete_file(new_file_path)
            raise

        if new_file_path is not None:
            self.file_storage_service.delete_file(old_file_path)
        return document

    # Delete both the database record and its stored file.
    def delete_document(self, document_id):
        """Delete a document and return whether its record was removed."""
        document = self.get_document(document_id)
        if document is None:
            return False

        if not document.delete():
            return False

        # A missing file is harmless because the database record is already
        # gone and there is no file left to remove.
        self.file_storage_service.delete_file(document.file_path)
        return True

    # Resolve a document's stored file for a download route.
    def get_document_file(self, document_id):
        """Return the stored file path for a document."""
        document = self.get_document(document_id)
        if document is None:
            return None
        return self.file_storage_service.get_file(document.file_path)
