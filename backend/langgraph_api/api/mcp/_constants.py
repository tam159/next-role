# Workaround assistant name not exposed in the Assistants.search API
MAX_ASSISTANTS = 1000
DEFAULT_PAGE_SIZE = 100

# JSON-RPC error codes: https://www.jsonrpc.org/specification#error_object
ERROR_CODE_INVALID_PARAMS = -32602
ERROR_CODE_METHOD_NOT_FOUND = -32601

# Supported MCP protocol versions
# https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    (
        "2024-11-05",
        "2025-03-26",
        "2025-06-18",
        "2025-11-25",
    )
)
LATEST_PROTOCOL_VERSION = "2025-11-25"
