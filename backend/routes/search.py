"""HTTP route that serves the prefix-tree search index."""

from flask import Blueprint, jsonify

from backend.models import Document
from backend.routes.auth import auth_error, get_current_user
from backend.services.trie_search_service import TrieSearchService


search_bp = Blueprint("search", __name__, url_prefix="/api/v1/search")


@search_bp.get("/trie")
def get_search_trie():
    """Build and return the trie as JSON for client-side prefix search."""
    user = get_current_user()
    if user is None:
        return auth_error()
    documents = Document.find_all()
    if user.get("role", "").upper() != "ADMIN":
        documents = [document for document in documents if document.receiver == user.get("name")]
    trie_service = TrieSearchService()
    trie_service.build_from_documents(documents)
    return jsonify({"trie": trie_service.to_dict(), "count": len(documents)})
