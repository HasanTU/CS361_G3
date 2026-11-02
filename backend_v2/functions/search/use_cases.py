"""Prefix tree (Trie) search use cases and index builder."""

import time
from typing import Any, Callable, Optional

from backend_v2.shared.ports.document_repository import DocumentRepositoryPort


class TrieNode:
    """Single node in the prefix tree."""

    __slots__ = ("children", "doc_ids")

    def __init__(self):
        self.children: dict[str, TrieNode] = {}
        self.doc_ids: set[str] = set()


class TrieSearchIndex:
    """In-memory prefix tree indexing document metadata for client-side search."""

    def __init__(self):
        self.root = TrieNode()

    def insert(self, text: Optional[str], doc_id: str) -> None:
        """Insert words and all character suffixes to support unsegmented language searches."""
        if not text or not isinstance(text, str):
            return
        doc_id_str = str(doc_id).strip()
        cleaned = text.strip().lower()
        words = cleaned.split()

        for word in words:
            # Index all suffixes so compound words (e.g., Thai phrases) match prefixes
            for i in range(len(word)):
                node = self.root
                suffix = word[i:]
                for char in suffix:
                    if char not in node.children:
                        node.children[char] = TrieNode()
                    node = node.children[char]
                node.doc_ids.add(doc_id_str)

    def search(self, prefix: str) -> set[str]:
        """Return set of document IDs matching the query prefix."""
        if not prefix or not isinstance(prefix, str):
            return set()

        prefix_clean = prefix.strip().lower()
        node = self.root
        for char in prefix_clean:
            if char not in node.children:
                return set()
            node = node.children[char]

        results = set(node.doc_ids)
        stack = list(node.children.values())
        while stack:
            curr = stack.pop()
            results.update(curr.doc_ids)
            stack.extend(curr.children.values())
        return results

    def to_dict(self) -> dict[str, Any]:
        """Serialize the trie hierarchy into a compact JSON-compatible dictionary."""

        def _serialize(node: TrieNode) -> dict[str, Any]:
            result: dict[str, Any] = {}
            if node.doc_ids:
                result["$"] = sorted(list(node.doc_ids))
            for char, child in node.children.items():
                result[char] = _serialize(child)
            return result

        return _serialize(self.root)

    def build_from_documents(self, documents: list[Any]) -> None:
        """Extract and index title, sender, and receiver for all documents."""
        for doc in documents:
            doc_id = str(getattr(doc, "id", "") or "")
            if not doc_id:
                continue
            self.insert(getattr(doc, "title", ""), doc_id)
            self.insert(getattr(doc, "sender", ""), doc_id)
            self.insert(getattr(doc, "receiver", ""), doc_id)


class SearchUseCases:
    """Orchestrator for document search operations adhering to Clean Architecture."""

    # Per-container trie cache: keyed by (caller_sub, is_admin) so USER caches
    # are isolated from each other and from the ADMIN view.
    _TRIE_TTL_SECONDS: float = 30.0

    def __init__(
        self,
        repository: DocumentRepositoryPort,
        trie_ttl_seconds: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._repo = repository
        self._ttl = trie_ttl_seconds if trie_ttl_seconds is not None else self._TRIE_TTL_SECONDS
        self._clock = clock
        # _trie_cache: (caller_sub, is_admin) -> (dict, count, cached_at)
        self._trie_cache: dict[tuple[str, bool], tuple[dict[str, Any], int, float]] = {}

    def invalidate_trie_cache(self) -> None:
        """Clear all cached trie entries.  Call after any document mutation."""
        self._trie_cache.clear()

    def get_search_trie(
        self, caller_sub: str, is_admin: bool = False
    ) -> tuple[dict[str, Any], int]:
        """Return a cached trie dict and document count, refreshed after TTL expires.

        Rebuilding the trie on every request requires a full MongoDB scan and
        O(n * avg_word_len * avg_word_count) insertions.  A 30-second in-process
        cache makes repeated searches free for warm Lambda containers.
        """
        cache_key = (caller_sub if not is_admin else "_admin", is_admin)
        now = self._clock()

        cached = self._trie_cache.get(cache_key)
        if cached is not None:
            trie_dict, count, cached_at = cached
            if now - cached_at < self._ttl:
                return trie_dict, count

        if is_admin:
            documents = self._repo.find_all()
        else:
            documents = self._repo.find_by_assigned_user(caller_sub)

        index = TrieSearchIndex()
        index.build_from_documents(documents)
        trie_dict = index.to_dict()
        count = len(documents)

        self._trie_cache[cache_key] = (trie_dict, count, now)
        return trie_dict, count
