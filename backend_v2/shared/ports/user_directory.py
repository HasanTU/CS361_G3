"""Abstract Port for looking up users of the identity provider."""

from abc import ABC, abstractmethod

from backend_v2.shared.domain.directory_user import DirectoryUser


class UserDirectoryPort(ABC):
    """Outbound port for listing the users that documents can be assigned to."""

    @abstractmethod
    def list_users(self) -> list[DirectoryUser]:
        """Return enabled users of the identity provider, sorted by (name.casefold(), sub)."""
        pass
