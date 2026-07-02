class RemoteException(Exception):
    error: str

    def __init__(self, error: str, *args: object) -> None:
        super().__init__(*args)
        self.error = error

    # Used to nudge the serde to encode like BaseException
    # @see /api/langgraph_api/shared/serde.py:default
    def dict(self):
        return {"error": self.error, "message": str(self)}
