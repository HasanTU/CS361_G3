"""Shared fixtures and helpers for the backend_v2 auth bypass tests.

Everything here is offline: no Cognito, MongoDB, S3 or network access.
The same helpers run against the unfixed code and the fixed code. Parts that
only exist after the fix (verifier, user directory port) are imported inside
try/except ImportError.
"""
import base64
import copy
import json
import shutil
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend_v2.shared.domain.document import Document
from backend_v2.shared.domain.document_status import DocumentStatus
from backend_v2.shared.domain.status_log_entry import StatusLogEntry
from backend_v2.shared.ports.document_repository import DocumentRepositoryPort
from backend_v2.shared.ports.file_storage import FileStoragePort

TEST_POOL_ID = "us-east-1_TestPool1"
TEST_CLIENT_ID = "test-client-id"
TEST_KID = "test-kid-1"
TEST_ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TestPool1"

# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def rsa_private_key():
    return _RSA_KEY


@pytest.fixture(scope="session")
def other_private_key():
    return _OTHER_KEY


# ---------------------------------------------------------------------------
# Seed users (values copied from SEED_USERS in aws_deploy/setup_cognito.py)
# ---------------------------------------------------------------------------

_SEED_SOURCE = [
    ("staff1@tu.ac.th", "คุณธารินี สุขสวัสดิ์", "ADMIN", "สำนักงานธุรการ (STAFF-1)"),
    ("head1@tu.ac.th", "อ.ดร.ปกรณ์ สุขใจ", "USER", "หัวหน้าสาขาวิชาวิทยาการคอมพิวเตอร์ (HEAD-1)"),
    ("fac_a@tu.ac.th", "อ.ดร.วรินทร์ พงษ์ดี", "USER", "คณะวิทยาศาสตร์และเทคโนโลยี (FAC-A)"),
    ("fac_b@tu.ac.th", "อ.ดร.ณัฐวุฒิ ศรีวัฒน์", "USER", "คณะวิทยาศาสตร์และเทคโนโลยี (FAC-B)"),
    ("admin1@tu.ac.th", "Kasidit", "ADMIN", "สำนักงานธุรการ"),
    ("admin2@tu.ac.th", "Saowaluk", "ADMIN", "สำนักงานเลขานุการคณะ"),
    ("user1@tu.ac.th", "Prapaporn", "USER", "คณะวิทยาศาสตร์และเทคโนโลยี"),
    ("user2@tu.ac.th", "Songsakdi", "USER", "คณะศิลปศาสตร์"),
    ("user3@tu.ac.th", "Wilawan", "USER", "คณะวิศวกรรมศาสตร์"),
]

SEED_USERS: list[dict[str, str]] = [
    {
        "sub": f"00000000-0000-4000-8000-{index:012d}",
        "email": email,
        "name": name,
        "group": group,
        "faculty": faculty,
    }
    for index, (email, name, group, faculty) in enumerate(_SEED_SOURCE, start=1)
]
SEED_BY_EMAIL: dict[str, dict[str, str]] = {u["email"]: u for u in SEED_USERS}


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def default_claims(claims_overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Default ID-token claims. An override value of None deletes the claim."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": "00000000-0000-4000-8000-0000000000aa",
        "email": "user@example.com",
        "name": "Test User",
        "cognito:groups": ["USER"],
        "iss": TEST_ISSUER,
        "aud": TEST_CLIENT_ID,
        "token_use": "id",
        "iat": now,
        "exp": now + 3600,
    }
    for key, value in (claims_overrides or {}).items():
        if value is None:
            claims.pop(key, None)
        else:
            claims[key] = value
    return claims


def make_token(
    claims_overrides: Optional[dict[str, Any]] = None,
    key: Any = None,
    kid: str = TEST_KID,
    alg: str = "RS256",
) -> str:
    claims = default_claims(claims_overrides)
    return jwt.encode(claims, key or _RSA_KEY, algorithm=alg, headers={"kid": kid})


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_none_token(claims: dict[str, Any]) -> str:
    """Unsigned token in the same format as AuthUseCases._synthesize_test_token."""
    header = _b64url(b'{"alg":"none","typ":"JWT"}')
    payload = _b64url(json.dumps(claims).encode())
    return f"{header}.{payload}.mock_signature"


def make_user_token(email: str, claims_overrides: Optional[dict[str, Any]] = None, **kwargs: Any) -> str:
    """Signed ID token for one of the nine seed users."""
    user = SEED_BY_EMAIL[email]
    overrides: dict[str, Any] = {
        "sub": user["sub"],
        "email": user["email"],
        "name": user["name"],
        "cognito:groups": [user["group"]],
        "custom:faculty": user["faculty"],
    }
    overrides.update(claims_overrides or {})
    return make_token(overrides, **kwargs)


class StaticSigningKeyProvider:
    """Offline replacement for jwt.PyJWKClient."""

    def __init__(self, keys: dict[str, Any]):
        self._keys = keys

    def get_signing_key_from_jwt(self, token: str) -> Any:
        kid = jwt.get_unverified_header(token).get("kid")
        if kid not in self._keys:
            raise jwt.PyJWKClientError("Unable to find a signing key")
        return types.SimpleNamespace(key=self._keys[kid])


class ConnectionErrorSigningKeyProvider:
    """Signing key provider that behaves as if the JWKS endpoint is unreachable."""

    def get_signing_key_from_jwt(self, token: str) -> Any:
        raise jwt.PyJWKClientConnectionError("Fail to fetch data from the url")


def build_verifier() -> Any:
    from backend_v2.shared.auth.token_verifier import CognitoTokenVerifier

    return CognitoTokenVerifier(
        TEST_POOL_ID,
        TEST_CLIENT_ID,
        signing_key_provider=StaticSigningKeyProvider({TEST_KID: _RSA_KEY.public_key()}),
    )


@pytest.fixture
def verifier():
    return build_verifier()


@pytest.fixture(autouse=True)
def install_test_verifier(monkeypatch):
    monkeypatch.setenv("COGNITO_USER_POOL_ID", TEST_POOL_ID)
    monkeypatch.setenv("COGNITO_CLIENT_ID", TEST_CLIENT_ID)
    try:
        from backend_v2.shared.auth import set_token_verifier
    except ImportError:
        yield
        return
    set_token_verifier(build_verifier())
    try:
        yield
    finally:
        set_token_verifier(None)


# ---------------------------------------------------------------------------
# User directory
# ---------------------------------------------------------------------------

try:
    from backend_v2.shared.domain.directory_user import DirectoryUser as _DirectoryUser
except ImportError:
    @dataclass(frozen=True)
    class _DirectoryUser:  # type: ignore[no-redef]
        sub: str
        email: str
        name: str

try:
    from backend_v2.shared.ports.user_directory import UserDirectoryPort as _DirectoryBase
except ImportError:
    _DirectoryBase = object  # type: ignore[misc,assignment]


class FakeUserDirectory(_DirectoryBase):  # type: ignore[valid-type,misc]
    """Fixed directory of the nine seed users, each with a fixed UUID sub."""

    def list_users(self) -> list[Any]:
        users = [_DirectoryUser(sub=u["sub"], email=u["email"], name=u["name"]) for u in SEED_USERS]
        return sorted(users, key=lambda u: (u.name.casefold(), u.sub))


# ---------------------------------------------------------------------------
# In-memory adapters
# ---------------------------------------------------------------------------

class InMemoryDocumentRepo(DocumentRepositoryPort):
    def __init__(self) -> None:
        self._docs: dict[str, Document] = {}
        self._counter = 0
        self.raise_on_find_all: Optional[Exception] = None

    def _next_id(self) -> str:
        self._counter += 1
        return f"doc-{self._counter}"

    def add(self, document: Document) -> Document:
        """Seed helper: store a document and give it the next id."""
        document.id = self._next_id()
        self._docs[document.id] = copy.deepcopy(document)
        return copy.deepcopy(document)

    def get_by_id(self, document_id: str) -> Optional[Document]:
        doc = self._docs.get(str(document_id))
        return copy.deepcopy(doc) if doc is not None else None

    def find_all(self, filter_criteria: Optional[dict[str, Any]] = None) -> list[Document]:
        if self.raise_on_find_all is not None:
            raise self.raise_on_find_all
        return [copy.deepcopy(d) for d in self._docs.values()]

    def find_by_assigned_user(self, value: Any) -> list[Document]:
        if isinstance(value, (list, tuple, set)):
            wanted = {str(v) for v in value}
        elif isinstance(value, str) and value.strip():
            wanted = {value.strip()}
        else:
            return []
        return [
            copy.deepcopy(d) for d in self._docs.values() if wanted.intersection(d.assigned_to)
        ]

    def create(self, document: Document) -> Document:
        return self.add(document)

    def update(self, document_id: str, changes: dict[str, Any]) -> bool:
        doc = self._docs.get(str(document_id))
        if doc is None:
            return False
        for key, value in changes.items():
            if key == "status":
                doc.status = DocumentStatus(value)
            elif key == "status_history":
                doc.status_history = [
                    e if isinstance(e, StatusLogEntry) else StatusLogEntry.from_dict(e)
                    for e in value
                ]
            elif key == "assigned_to":
                doc.assigned_to = list(value)
            else:
                setattr(doc, key, value)
        return True

    def delete(self, document_id: str) -> bool:
        return self._docs.pop(str(document_id), None) is not None


class InMemoryFileStorage(FileStoragePort):
    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._counter = 0

    def save_file(
        self, file_name: str, file_bytes: bytes, content_type: str = "application/octet-stream"
    ) -> dict[str, Any]:
        self._counter += 1
        stored = self._root / f"{self._counter}_{Path(file_name).name}"
        stored.write_bytes(file_bytes)
        return {
            "file_path": str(stored),
            "file_name": Path(file_name).name,
            "file_type": content_type,
            "file_size": len(file_bytes),
        }

    def get_file(self, file_path: str) -> Path:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(file_path)
        return path

    def delete_file(self, file_path: str) -> bool:
        Path(file_path).unlink(missing_ok=True)
        return True

    def stored_files(self) -> list[Path]:
        return sorted(p for p in self._root.iterdir() if p.is_file())


def build_document_use_cases(repo: Any, storage: Any, directory: Any) -> Any:
    from backend_v2.functions.documents.use_cases import DocumentUseCases

    try:
        return DocumentUseCases(repository=repo, storage=storage, user_directory=directory)
    except TypeError:
        return DocumentUseCases(repository=repo, storage=storage)


def seed_document(
    repo: InMemoryDocumentRepo,
    storage: InMemoryFileStorage,
    *,
    assigned_to: list[str],
    receiver: str,
    title: str = "Test document",
    sender: str = "Sender Office",
    file_name: str = "test.pdf",
    file_bytes: bytes = b"%PDF-1.4 test",
    uploaded_by: str = "staff1@tu.ac.th",
) -> Document:
    meta = storage.save_file(file_name, file_bytes, "application/pdf")
    doc = Document(
        title=title,
        document_date="2025-01-15",
        sender=sender,
        receiver=receiver,
        file_path=meta["file_path"],
        file_name=meta["file_name"],
        file_type=meta["file_type"],
        file_size=meta["file_size"],
        uploaded_by=uploaded_by,
        status=DocumentStatus.ASSIGNED if assigned_to else DocumentStatus.RECEIVED,
        assigned_to=assigned_to,
    )
    return repo.add(doc)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def make_event(
    method: str,
    path: str,
    token: Optional[str] = None,
    via: str = "bearer",
    body: Any = None,
    headers: Optional[dict[str, str]] = None,
    query: Optional[dict[str, str]] = None,
    is_base64: bool = False,
) -> dict[str, Any]:
    """API Gateway HTTP API v2 event, as read by the lambda_handler functions."""
    event_headers: dict[str, str] = dict(headers or {})
    cookies: list[str] = []
    if token is not None:
        if via == "bearer":
            event_headers["authorization"] = f"Bearer {token}"
        elif via == "cookies":
            cookies.append(f"auth_token={token}")
        elif via == "cookie_header":
            event_headers["cookie"] = f"auth_token={token}"
        else:
            raise ValueError(f"unknown token location: {via}")
    if body is not None and not isinstance(body, (str, bytes)):
        body = json.dumps(body)
    event: dict[str, Any] = {
        "requestContext": {"http": {"method": method}},
        "rawPath": path,
        "headers": event_headers,
        "cookies": cookies,
        "queryStringParameters": query or {},
        "body": body,
        "isBase64Encoded": is_base64,
    }
    return event


def make_multipart(
    fields: dict[str, str], file_name: str = "letter.pdf", file_text: str = "%PDF-1.4 letter"
) -> tuple[str, str]:
    """Return (content_type, body) for a multipart/form-data upload."""
    boundary = "----testboundary7MA4YWxkTrZu0gW"
    parts: list[str] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
        f"{file_text}\r\n"
    )
    parts.append(f"--{boundary}--\r\n")
    return f"multipart/form-data; boundary={boundary}", "".join(parts)


def body_of(response: dict[str, Any]) -> Any:
    raw = response.get("body")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def error_message(response: dict[str, Any]) -> Optional[str]:
    body = body_of(response)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"].get("message")
    return None


def describe(response: dict[str, Any]) -> str:
    """Short text of a response for assertion messages."""
    text = str(response.get("body"))
    if len(text) > 300:
        text = text[:300] + "..."
    return f"status={response.get('statusCode')} body={text}"


# ---------------------------------------------------------------------------
# Fake Cognito client
# ---------------------------------------------------------------------------

class FakeCognitoClient:
    """initiate_auth returns an IdToken, or raises the configured exception."""

    def __init__(self, id_token: Optional[str] = None, error: Optional[Exception] = None) -> None:
        self.id_token = id_token
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def initiate_auth(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"AuthenticationResult": {"IdToken": self.id_token}}

    @staticmethod
    def client_error(code: str) -> Exception:
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": code, "Message": "x"}}, "InitiateAuth")

    @staticmethod
    def connection_error() -> Exception:
        from botocore.exceptions import EndpointConnectionError

        return EndpointConnectionError(endpoint_url="https://cognito-idp")


def build_auth_use_cases(cognito_client: Any) -> Any:
    """AuthUseCases with only the keyword arguments that exist before and after the fix."""
    from backend_v2.functions.auth.use_cases import AuthUseCases

    use_cases = AuthUseCases(cognito_client=cognito_client, cognito_client_id="x")
    if hasattr(use_cases, "_get_user_collection"):
        # Unfixed code only: avoid a 30 second wait for a local MongoDB.
        use_cases._get_user_collection = lambda: None  # type: ignore[method-assign]
    return use_cases


# ---------------------------------------------------------------------------
# Handler wiring
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    """In-memory repo and storage wired into the documents and search handlers."""
    from backend_v2.functions.documents import handler as documents_handler
    from backend_v2.functions.search import handler as search_handler
    from backend_v2.functions.search.use_cases import SearchUseCases

    repo = InMemoryDocumentRepo()
    storage = InMemoryFileStorage(tmp_path / "files")
    directory = FakeUserDirectory()
    documents_handler.set_use_cases(build_document_use_cases(repo, storage, directory))
    search_handler.set_use_cases(SearchUseCases(repository=repo))
    try:
        yield types.SimpleNamespace(
            repo=repo,
            storage=storage,
            directory=directory,
            documents=documents_handler.lambda_handler,
            search=search_handler.lambda_handler,
        )
    finally:
        documents_handler.set_use_cases(None)  # type: ignore[arg-type]
        search_handler.set_use_cases(None)  # type: ignore[arg-type]
        shutil.rmtree(tmp_path / "files", ignore_errors=True)


@pytest.fixture
def auth_login():
    """Factory: wire AuthUseCases around a fake Cognito client and return the auth lambda_handler."""
    from backend_v2.functions.auth import handler as auth_handler

    def _wire(cognito_client: Any) -> Any:
        auth_handler.set_use_cases(build_auth_use_cases(cognito_client))
        return auth_handler.lambda_handler

    try:
        yield _wire
    finally:
        auth_handler.set_use_cases(None)  # type: ignore[arg-type]


class SpyUseCases:
    """Records every use case call. Used to prove that no use case ran."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)

        def _record(*args: Any, **kwargs: Any) -> None:
            self.calls.append(name)
            return None

        return _record
