"""Idempotently create local users for authentication testing."""

import json
from pathlib import Path

from backend.models.database import db


MOCK_USERS_FILE = Path(__file__).resolve().parents[1] / "data" / "mock_users.json"


def seed_users():
    """Create mock users once when the USERS collection is empty."""
    collection = db["users"]
    collection.create_index("email", unique=True, name="uq_users_email")
    if collection.count_documents({}) > 0:
        return False

    mock_users = json.loads(MOCK_USERS_FILE.read_text(encoding="utf-8"))
    collection.insert_many(mock_users)
    return True


if __name__ == "__main__":
    print("Mock users seeded" if seed_users() else "Mock users already exist")
