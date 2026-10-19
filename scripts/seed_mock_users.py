"""Idempotently create local users for authentication testing."""

import json
from pathlib import Path

from backend.models.database import db


MOCK_USERS_FILE = Path(__file__).resolve().parents[1] / "data" / "mock_users.json"


def seed_users():
    """Create or update only the named mock users and ensure uniqueness."""
    collection = db["users"]
    collection.create_index("email", unique=True, name="uq_users_email")
    mock_users = json.loads(MOCK_USERS_FILE.read_text(encoding="utf-8"))
    for mock_user in mock_users:
        collection.update_one(
            {"email": mock_user["email"]},
            {
                "$set": {
                    "name": mock_user["name"],
                    "role": mock_user["role"],
                    "faculty": mock_user["faculty"],
                    "password_hash": mock_user["password_hash"],
                },
                "$setOnInsert": {"email": mock_user["email"]},
            },
            upsert=True,
        )


if __name__ == "__main__":
    seed_users()
    print("Mock users seeded")
