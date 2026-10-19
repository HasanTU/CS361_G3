"""Application configuration loaded from environment variables."""

# Import os to read environment variables.
import os


# Store the MongoDB connection string used by the backend.
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/e_mailbox")

# Sign the server-side login session cookie. Set a stable value in deployment.
SECRET_KEY = os.getenv("SECRET_KEY", "e-mailbox-development-secret")

# Store the directory used for uploaded documents.
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")

# Enable only for local mock authentication; production should disable this
# when authentication is delegated to the Thammasat API.
SEED_MOCK_USERS = os.getenv("SEED_MOCK_USERS", "false").lower() == "true"
SEED_MOCK_DOCUMENTS = os.getenv("SEED_MOCK_DOCUMENTS", "false").lower() == "true"
