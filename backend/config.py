"""Application configuration loaded from environment variables."""

# Import os to read environment variables.
import os


# Store the MongoDB connection string used by the backend.
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/e_mailbox")

# Store the directory used for uploaded documents.
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")

# Enable only for local mock authentication; production should disable this
# when authentication is delegated to the Thammasat API.
SEED_MOCK_USERS = os.getenv("SEED_MOCK_USERS", "false").lower() == "true"
