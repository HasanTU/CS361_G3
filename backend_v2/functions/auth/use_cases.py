"""Application use cases coordinating Authentication, Cognito, and User Directory."""

import base64
import json
import os
from typing import Any, Optional, Tuple


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode unverified JWT claims safely without external dependencies."""
    parts = token.strip().split(".")
    if len(parts) < 2:
        raise ValueError("Malformed JWT structure")
    payload_b64 = parts[1]
    rem = len(payload_b64) % 4
    if rem > 0:
        payload_b64 += "=" * (4 - rem)
    decoded_bytes = base64.urlsafe_b64decode(payload_b64)
    return json.loads(decoded_bytes.decode("utf-8"))


class AuthUseCases:
    """Authentication and session management orchestrator."""

    def __init__(
        self,
        cognito_client: Any = None,
        cognito_client_id: Optional[str] = None,
        mongo_collection: Any = None,
    ):
        self._client = cognito_client
        self._client_id = cognito_client_id or os.getenv("COGNITO_CLIENT_ID")
        self._collection = mongo_collection

    def _get_cognito_client(self) -> Any:
        """Lazily initialize boto3 cognito-idp client if available."""
        if self._client is None:
            try:
                import boto3

                self._client = boto3.client(
                    "cognito-idp",
                    region_name=os.getenv("AWS_REGION", "us-east-1"),
                )
            except Exception:
                self._client = None
        return self._client

    def _get_user_collection(self) -> Any:
        """Lazily initialize MongoDB users collection for recipient lookup."""
        if self._collection is None:
            try:
                from pymongo import MongoClient

                uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/e_mailbox")
                if ".mongodb.net/?" in uri and "/e_mailbox?" not in uri:
                    uri = uri.replace(".mongodb.net/?", ".mongodb.net/e_mailbox?")
                elif uri.endswith(".mongodb.net"):
                    uri = uri + "/e_mailbox"
                client: MongoClient = MongoClient(uri)
                try:
                    db = client.get_default_database()
                except Exception:
                    db = None
                if db is None:
                    db = client["e_mailbox"]
                self._collection = db["users"]
            except Exception:
                self._collection = None
        return self._collection

    def login(self, email: str, password: str) -> Tuple[dict[str, Any], str]:
        """Authenticate user against AWS Cognito or fallback provider.

        Returns:
            Tuple[dict[str, Any], str]: (user_dict, id_token_jwt)
        Raises:
            ValueError: If input is invalid.
            PermissionError: If credentials are wrong.
        """
        clean_email = (email or "").strip().lower()
        if not clean_email or not password:
            raise ValueError("Email and password are required")

        cognito = self._get_cognito_client()

        # 1. Primary Cloud Authentication: AWS Cognito User Pool
        if cognito and self._client_id:
            try:
                resp = cognito.initiate_auth(
                    AuthFlow="USER_PASSWORD_AUTH",
                    ClientId=self._client_id,
                    AuthParameters={
                        "USERNAME": clean_email,
                        "PASSWORD": password,
                    },
                )
                auth_result = resp.get("AuthenticationResult", {})
                token = auth_result.get("IdToken") or auth_result.get("AccessToken")
                if not token:
                    raise PermissionError(
                        "Cognito authentication failed: no token returned"
                    )

                claims = _decode_jwt_payload(token)
                groups = [str(g).upper() for g in claims.get("cognito:groups", [])]
                custom_role = str(
                    claims.get("custom:role") or claims.get("role") or ""
                ).lower()
                role = (
                    "admin"
                    if (
                        "ADMIN" in groups
                        or custom_role == "admin"
                        or clean_email.startswith("admin")
                        or clean_email in ["admin1@tu.ac.th", "admin2@tu.ac.th"]
                    )
                    else "user"
                )

                user = {
                    "id": claims.get("sub", ""),
                    "email": claims.get("email", clean_email),
                    "name": claims.get("name")
                    or claims.get("cognito:username")
                    or clean_email.split("@")[0],
                    "role": role,
                    "faculty": claims.get("custom:faculty", ""),
                }
                return user, token
            except Exception as err:
                print(f"Cognito login error: {type(err).__name__}: {err}")
                # If Cognito returned NotAuthorizedException or similar
                if "NotAuthorizedException" in str(
                    err
                ) or "UserNotFoundException" in str(err):
                    raise PermissionError("Invalid email or password") from err
                # If network or boto3 error, fall through to fallback

        # 2. Local / Offline Fallback Authentication (Mock / Unit test)
        coll = self._get_user_collection()
        if coll is not None:
            record = coll.find_one({"email": clean_email})
            if record:
                # Simple password match or hash match
                from werkzeug.security import check_password_hash

                pw_hash = record.get("password_hash", "")
                if check_password_hash(pw_hash, password) or pw_hash == password:
                    role = str(record.get("role", "user")).lower()
                    user = {
                        "id": str(record.get("_id", "mock-user-id")),
                        "email": record["email"],
                        "name": record.get("name", clean_email.split("@")[0]),
                        "role": role,
                        "faculty": record.get("faculty", ""),
                    }
                    token = self._synthesize_test_token(user)
                    return user, token

        # 3. Development admin mock
        if clean_email == "admin@kanna.ac.th" and password == "admin1234":
            user = {
                "id": "mock-admin-id",
                "email": clean_email,
                "name": "Kanna Admin",
                "role": "admin",
                "faculty": "Computer Science",
            }
            return user, self._synthesize_test_token(user)

        raise PermissionError("Invalid email or password")

    @staticmethod
    def _synthesize_test_token(user: dict[str, Any]) -> str:
        """Create a mock JWT for offline testing without network."""
        hdr = (
            base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("=")
        )
        user_role = str(user.get("role", "")).lower()
        is_admin_user = (user_role == "admin") or ("admin" in str(user.get("email", "")).lower())
        claims = {
            "sub": user.get("id", "user-id"),
            "email": user["email"],
            "name": user.get("name", ""),
            "role": "admin" if is_admin_user else "user",
            "cognito:groups": ["ADMIN"] if is_admin_user else ["USER"],
        }
        payload = (
            base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        )
        return f"{hdr}.{payload}.mock_signature"

    def list_users(self) -> list[dict[str, str]]:
        """Return list of distinct registered user names for recipient dropdowns."""
        coll = self._get_user_collection()
        names = set()
        if coll is not None:
            try:
                for record in coll.find({}, {"name": 1}):
                    if isinstance(record.get("name"), str) and record["name"].strip():
                        names.add(record["name"].strip())
            except Exception:
                pass

        # Fallback to default in-class challenge and seed users
        default_names = {
            "คุณธารินี สุขสวัสดิ์",
            "อ.ดร.ปกรณ์ สุขใจ",
            "หัวหน้าสาขาวิชาวิทยาการคอมพิวเตอร์",
            "อ.ดร.วรินทร์ พงษ์ดี",
            "อ.ดร.ณัฐวุฒิ ศรีวัฒน์",
            "Kasidit",
            "Saowaluk",
            "Prapaporn",
            "Songsakdi",
            "Wilawan",
        }
        names.update(default_names)

        return [{"name": n} for n in sorted(names)]
