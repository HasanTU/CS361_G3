"""Verification of Cognito ID tokens (RS256, JWKS, iss, aud, exp, token_use)."""
import os
from typing import Any

import jwt

from backend_v2.shared.auth.errors import (
    AUTH_REQUIRED_MESSAGE,
    INVALID_TOKEN_MESSAGE,
    SERVICE_UNAVAILABLE_MESSAGE,
    AuthenticationError,
    AuthServiceUnavailableError,
)


class CognitoTokenVerifier:
    """Verifies ID tokens issued by one Cognito user pool for one app client."""

    ALGORITHM = "RS256"

    def __init__(
        self,
        user_pool_id: str,
        client_id: str,
        signing_key_provider: Any = None,
        leeway_seconds: int = 10,
    ):
        region, separator, pool_suffix = (user_pool_id or "").partition("_")
        if not separator or not region or not pool_suffix:
            raise RuntimeError("COGNITO_USER_POOL_ID is missing or malformed")
        if not client_id:
            raise RuntimeError("COGNITO_CLIENT_ID is missing")

        self._user_pool_id = user_pool_id
        self._client_id = client_id
        self._leeway = leeway_seconds
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self.jwks_url = f"{self.issuer}/.well-known/jwks.json"
        self._keys = signing_key_provider or jwt.PyJWKClient(
            self.jwks_url, cache_jwk_set=True, lifespan=3600, timeout=5
        )

    @classmethod
    def from_env(cls) -> "CognitoTokenVerifier":
        return cls(
            os.getenv("COGNITO_USER_POOL_ID", ""),
            os.getenv("COGNITO_CLIENT_ID", ""),
        )

    def verify(self, token: str) -> dict[str, Any]:
        """Return the verified claims, or raise AuthenticationError / AuthServiceUnavailableError."""
        # 1. Missing token
        if not token or not isinstance(token, str):
            raise AuthenticationError(AUTH_REQUIRED_MESSAGE)

        # 2. Header
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AuthenticationError(INVALID_TOKEN_MESSAGE) from None

        # 3. Algorithm and key id, before any network call
        if header.get("alg") != self.ALGORITHM or not header.get("kid"):
            raise AuthenticationError(INVALID_TOKEN_MESSAGE)

        # 4. Signing key
        try:
            signing_key = self._keys.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientConnectionError:
            raise AuthServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE) from None
        except jwt.PyJWTError:
            raise AuthenticationError(INVALID_TOKEN_MESSAGE) from None

        # 5. Signature and registered claims
        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=[self.ALGORITHM],
                audience=self._client_id,
                issuer=self.issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "token_use"]},
            )
        except jwt.PyJWTError:
            raise AuthenticationError(INVALID_TOKEN_MESSAGE) from None

        # 6. Only ID tokens
        if claims.get("token_use") != "id":
            raise AuthenticationError(INVALID_TOKEN_MESSAGE)

        # 7.
        return claims
