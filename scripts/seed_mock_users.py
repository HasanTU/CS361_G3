"""Idempotently create local users for authentication testing."""

import json
from pathlib import Path

from backend.models.database import db


MOCK_USERS_FILE = Path(__file__).resolve().parents[1] / "data" / "mock_users.json"


def seed_users(force=False):
    """Create users on first startup or refresh them when run manually."""
    collection = db["users"]
    collection.create_index("email", unique=True, name="uq_users_email")
    if collection.count_documents({}) > 0 and not force:
        return False

    mock_users = json.loads(MOCK_USERS_FILE.read_text(encoding="utf-8"))
    for mock_user in mock_users:
        collection.update_one(
            {"email": mock_user["email"]},
            {"$set": mock_user},
            upsert=True,
        )
    return True


if __name__ == "__main__":
    print("Mock users refreshed" if seed_users(force=True) else "Mock users already exist")
