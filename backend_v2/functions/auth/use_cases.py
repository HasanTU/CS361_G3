"""Application use cases coordinating Authentication, Cognito, and User Directory."""

import logging
import os
from typing import Any, Optional, Tuple

from botocore.exceptions import BotoCoreError, ClientError

from backend_v2.shared.auth import (
    SERVICE_UNAVAILABLE_MESSAGE,
    AuthenticationError,
    AuthServiceUnavailableError,
    CognitoTokenVerifier,
    caller_from_claims,
    get_token_verifier,
)
from backend_v2.shared.ports.user_directory import UserDirectoryPort

logger = logging.getLogger(__name__)

INVALID_CREDENTIAL_CODES = frozenset(
    {
        "NotAuthorizedException",
        "UserNotFoundException",
        "UserNotConfirmedException",
        "PasswordResetRequiredException",
    }
)


class AuthUseCases:
    """Authentication and session management orchestrator."""

    def __init__(
        self,
        cognito_client: Any = None,
        cognito_client_id: Optional[str] = None,
        token_verifier: Optional[CognitoTokenVerifier] = None,
        user_directory: Optional[UserDirectoryPort] = None,
    ):
        self._client = cognito_client
        self._client_id = cognito_client_id or os.getenv("COGNITO_CLIENT_ID")
        self._verifier = token_verifier
        self._directory = user_directory

    def _get_cognito_client(self) -> Any:
        """Lazily initialize the boto3 cognito-idp client."""
        if self._client is None:
            try:
                import boto3

                self._client = boto3.client(
                    "cognito-idp",
                    region_name=os.getenv("AWS_REGION", "us-east-1"),
                )
            except Exception as err:
                logger.error(
                    "Could not create Cognito client: %s", type(err).__name__
                )
                raise AuthServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE) from None
        return self._client

    def login(self, email: str, password: str) -> Tuple[dict[str, Any], str]:
        """Authenticate a user against AWS Cognito only.

        Returns:
            Tuple[dict[str, Any], str]: (user_dict, id_token_jwt)
        Raises:
            ValueError: If input is invalid.
            PermissionError: If credentials are wrong.
            AuthServiceUnavailableError: If Cognito cannot authenticate the request.
        """
        clean_email = (email or "").strip().lower()
        if not clean_email or not password:
            raise ValueError("Email and password are required")

        if not self._client_id:
            logger.error("COGNITO_CLIENT_ID is not configured")
            raise AuthServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE)

        cognito = self._get_cognito_client()

        try:
            resp = cognito.initiate_auth(
                AuthFlow="USER_PASSWORD_AUTH",
                ClientId=self._client_id,
                AuthParameters={
                    "USERNAME": clean_email,
                    "PASSWORD": password,
                },
            )
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code", "")
            if code in INVALID_CREDENTIAL_CODES:
                raise AuthenticationError("Invalid email or password") from None
            logger.warning("Cognito initiate_auth failed with code %s", code)
            raise AuthServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE) from None
        except BotoCoreError as err:
            logger.warning("Cognito initiate_auth failed: %s", type(err).__name__)
            raise AuthServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE) from None

        id_token = (resp.get("AuthenticationResult") or {}).get("IdToken")
        if not id_token:
            # For example a ChallengeName response: no session can be issued.
            raise AuthenticationError("Invalid email or password")

        try:
            claims = (self._verifier or get_token_verifier()).verify(id_token)
        except AuthenticationError:
            logger.error("Cognito returned a token that failed verification")
            raise AuthServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE) from None

        caller = caller_from_claims(claims)

        user = {
            "id": caller.sub,
            "email": caller.email or clean_email,
            "name": caller.name
            or str(claims.get("cognito:username") or "")
            or clean_email.split("@")[0],
            "role": caller.role,
            "faculty": caller.faculty,
        }
        return user, id_token

    def list_users(self) -> list[dict[str, str]]:
        """Return the Cognito user directory as id and name pairs."""
        if self._directory is None:
            raise RuntimeError("User directory is not configured")
        return [{"id": u.sub, "name": u.name} for u in self._directory.list_users()]
