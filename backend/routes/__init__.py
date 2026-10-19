"""Application route blueprints."""

from backend.routes.documents import documents_bp
from backend.routes.search import search_bp
from backend.routes.auth import auth_bp

__all__ = ["documents_bp", "search_bp", "auth_bp"]
