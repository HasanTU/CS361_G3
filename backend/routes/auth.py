"""Authentication routes for local mock users."""

from flask import Blueprint, jsonify, request, session

from backend.models import User


auth_bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")


@auth_bp.post("/login")
def login():
    """Authenticate a user from the local USERS collection."""
    payload = request.get_json(silent=True) or {}
    email = payload.get("email")
    password = payload.get("password")
    if not isinstance(email, str) or not isinstance(password, str) or not email.strip() or not password:
        return jsonify({"error": {"message": "Email and password are required"}}), 400

    user = User.authenticate(email, password)
    if user is None:
        return jsonify({"error": {"message": "Invalid email or password"}}), 401
    session.clear()
    session["user_email"] = user["email"]
    return jsonify({"user": user})


@auth_bp.post("/logout")
def logout():
    """Clear the server-side login session."""
    session.clear()
    return jsonify({"message": "Logged out successfully"})


@auth_bp.get("/users")
def list_users():
    """Return database users that admins may select as document receivers."""
    user = get_current_user()
    if user is None:
        return auth_error()
    if user.get("role", "").upper() != "ADMIN":
        return admin_error()

    names = sorted(
        {
            record.get("name", "").strip()
            for record in User.collection.find({}, {"name": 1})
            if isinstance(record.get("name"), str) and record["name"].strip()
        }
    )
    return jsonify({"users": [{"name": name} for name in names]})


def get_current_user():
    """Load the authenticated user represented by the signed session cookie."""
    email = session.get("user_email")
    return User.find_by_email(email) if isinstance(email, str) else None


def auth_error():
    """Return the standard response for an unauthenticated API request."""
    return jsonify({"error": {"message": "Authentication is required"}}), 401


def admin_error():
    """Return the standard response for a non-admin API request."""
    return jsonify({"error": {"message": "Admin access is required"}}), 403


def is_known_user_name(name):
    """Return whether a receiver name exists in the USERS collection."""
    return (
        isinstance(name, str)
        and bool(name.strip())
        and User.collection.find_one({"name": name.strip()}, {"_id": 1}) is not None
    )
