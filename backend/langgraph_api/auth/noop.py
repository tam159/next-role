from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    BaseUser,
)
from starlette.authentication import (
    UnauthenticatedUser as StarletteUnauthenticatedUser,
)
from starlette.requests import HTTPConnection


class UnauthenticatedUser(StarletteUnauthenticatedUser):
    @property
    def identity(self) -> str:
        return ""

    def dict(self):
        return {
            "identity": self.identity,
            "is_authenticated": self.is_authenticated,
            "display_name": self.display_name,
        }

    def __getitem__(self, key):
        return self.dict()[key]

    def __contains__(self, key):
        return key in self.dict()

    def __iter__(self):
        return iter(self.dict())

    def __len__(self):
        return len(self.dict())

    def get(self, key, /, default=None):
        return self.dict().get(key, default)

    def keys(self):
        return self.dict().keys()

    def values(self):
        return self.dict().values()

    def items(self):
        return self.dict().items()


class NoopAuthBackend(AuthenticationBackend):
    async def authenticate(
        self, conn: HTTPConnection
    ) -> tuple[AuthCredentials, BaseUser] | None:
        return AuthCredentials(), UnauthenticatedUser()
