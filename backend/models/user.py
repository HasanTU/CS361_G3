"""Model and authentication helpers for application users."""

from werkzeug.security import check_password_hash

from backend.models.database import db


class User:
    """Represent a locally stored user profile used for authorization."""

    collection = db["users"]

    @classmethod
    def find_by_email(cls, email):
        """Find a user by its normalized email address."""
        return cls.collection.find_one({"email": email.strip().lower()})

    @classmethod
    def authenticate(cls, email, password):
        """Return a safe user profile when the password matches."""
        user = cls.find_by_email(email)
        if user is None or not check_password_hash(user["password_hash"], password):
            return None
        return {
            "id": str(user["_id"]),
            "email": user["email"],
            "name": user["name"],
            "role": user["role"].lower(),
            "faculty": user["faculty"],
        }
