from langgraph_sdk.auth.types import StudioUser as StudioUserBase
from starlette.authentication import BaseUser


class StudioUser(StudioUserBase, BaseUser):
    """StudioUser class."""

    def dict(self):
        return {
            "kind": "StudioUser",
            "is_authenticated": self.is_authenticated,
            "display_name": self.display_name,
            "identity": self.identity,
            "permissions": self.permissions,
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
