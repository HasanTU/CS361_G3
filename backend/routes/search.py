"""HTTP route that serves the prefix-tree search index."""

from flask import Blueprint, jsonify

from backend.models import Document
from backend.services.trie_search_service import TrieSearchService


search_bp = Blueprint("search", __name__, url_prefix="/api/v1/search")


@search_bp.get("/trie")
def get_search_trie():
    """Build and return the trie as JSON for client-side prefix search."""
    documents = Document.find_all()
    trie_service = TrieSearchService()
    trie_service.build_from_documents(documents)
    return jsonify({"trie": trie_service.to_dict(), "count": len(documents)})
