"""Prefix tree (Trie) search use cases and index builder."""

from typing import Any, Optional

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

    def __init__(self, repository: DocumentRepositoryPort):
        self._repo = repository

    def get_search_trie(
        self, user_id: str, is_admin: bool = False
    ) -> tuple[dict[str, Any], int]:
        """Retrieve documents according to role permissions and build the prefix tree."""
        if is_admin:
            documents = self._repo.find_all()
        else:
            documents = self._repo.find_by_assigned_user(user_id)

        index = TrieSearchIndex()
        index.build_from_documents(documents)
        return index.to_dict(), len(documents)
