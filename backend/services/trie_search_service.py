"""Prefix tree (Trie) search index for document metadata."""


class TrieNode:
    """Single node in the prefix tree."""
    __slots__ = ("children", "doc_ids")

    def __init__(self):
        self.children = {}
        self.doc_ids = set()


class TrieSearchService:
    """Build and query a prefix tree over document metadata."""

    def __init__(self):
        self.root = TrieNode()

    def insert(self, text, doc_id):
        """Insert each word and its suffixes of the lowercased text into the trie."""
        text = text.strip().lower()
        words = text.split()
        for word in words:
            # Index all suffixes so Thai compound words without spaces can match prefixes
            for i in range(len(word)):
                node = self.root
                for char in word[i:]:
                    if char not in node.children:
                        node.children[char] = TrieNode()
                    node = node.children[char]
                    node.doc_ids.add(doc_id)

    def search(self, prefix):
        """Return set of doc_ids matching the prefix."""
        prefix = prefix.strip().lower()
        node = self.root
        for char in prefix:
            if char not in node.children:
                return set()
            node = node.children[char]
        return set(node.doc_ids)

    def to_dict(self):
        """Serialize the trie to a JSON-compatible dict."""
        def _serialize(node):
            result = {}
            if node.doc_ids:
                result["$"] = list(node.doc_ids)
            for char, child in node.children.items():
                result[char] = _serialize(child)
            return result
        return _serialize(self.root)

    def build_from_documents(self, documents):
        """Index title, sender, and receiver for each document."""
        for doc in documents:
            doc_id = str(doc.id)
            self.insert(doc.title, doc_id)
            self.insert(doc.sender, doc_id)
            self.insert(doc.receiver, doc_id)
