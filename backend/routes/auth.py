"""Authentication routes for local mock users."""

from flask import Blueprint, jsonify, request

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
    return jsonify({"user": user})
