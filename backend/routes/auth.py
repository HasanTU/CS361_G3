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
