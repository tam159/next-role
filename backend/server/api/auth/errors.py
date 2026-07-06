from starlette.authentication import AuthenticationError


class AuthError(AuthenticationError):
    """Authentication failure that preserves the desired HTTP status code.

    Used so custom auth (e.g. Auth.exceptions.HTTPException(401)) can
    result in a 401 response instead of being normalized to 403.
    """

    def __init__(self, detail: str, status_code: int = 403):
        super().__init__(detail)
        self.status_code = status_code
