import asyncio
import copy
import functools
import importlib.util
import inspect
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from typing import Any, Literal, get_args

import structlog
from langgraph_sdk import Auth
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    BaseUser,
)
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import QueryParams
from starlette.exceptions import HTTPException
from starlette.requests import HTTPConnection, Request
from starlette.responses import Response

from langgraph_api import timing
from langgraph_api.auth.errors import AuthError
from langgraph_api.auth.langsmith.backend import LangsmithAuthBackend
from langgraph_api.auth.studio_user import StudioUser
from langgraph_api.config import LANGGRAPH_AUTH, LANGGRAPH_AUTH_TYPE
from langgraph_api.js.base import is_js_path
from langgraph_api.timing import profiled_import

logger = structlog.stdlib.get_logger(__name__)

SUPPORTED_PARAMETERS = {
    "request": Request,
    "body": dict,
    "user": BaseUser,
    "path": str,
    "method": str,
    "scopes": list[str],
    "path_params": dict[str, str] | None,
    "query_params": dict[str, str] | None,
    "headers": dict[bytes, bytes] | None,
    "authorization": str | None,
    "scope": dict[str, Any],
}


def get_custom_auth_middleware() -> AuthenticationBackend:
    """Load authentication function from a Python file or config dict."""
    if not LANGGRAPH_AUTH:
        raise ValueError(
            "LANGGRAPH_AUTH must be set to a Python file path or a config dict"
            " to use custom authentication."
        )
    logger.info("Using custom authentication", langgraph_auth=LANGGRAPH_AUTH)
    return _get_custom_auth_middleware(LANGGRAPH_AUTH)


@functools.lru_cache(maxsize=1)
def get_auth_instance() -> Auth | Literal["js"] | None:
    logger.info(
        f"Getting auth instance: {LANGGRAPH_AUTH}", langgraph_auth=str(LANGGRAPH_AUTH)
    )
    if not LANGGRAPH_AUTH:
        return None
    path = LANGGRAPH_AUTH.get("path")
    if path is None:
        return None
    return _get_auth_instance(path)


async def handle_event(
    ctx: Auth.types.AuthContext | None,
    value: dict,
) -> Auth.types.FilterType | None:
    """Run all handlers for a request.

    Returns:
    - A FilteredValue with the modified value and any filters to apply
    - Raises HTTPException(403) if any handler rejects the request

    Handlers are run in order from most specific to most general:
    1. Resource+action specific handlers (e.g. "threads", "create")
    2. Resource handlers (e.g. "threads", "*")
    3. Action handlers (e.g. "*", "create")
    4. Global handlers ("*", "*")
    """
    if ctx is None:
        return
    auth = get_auth_instance()
    if auth is None:
        return

    if auth == "js":
        from langgraph_api.js.remote import handle_js_auth_event  # noqa: PLC0415

        return await handle_js_auth_event(ctx, value)

    handler = _get_handler(auth, ctx)
    if not handler:
        return
    try:
        result = await handler(ctx=ctx, value=value)
    except Auth.exceptions.HTTPException as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
            headers=dict(e.headers) if e.headers else None,
        ) from e
    except AssertionError as e:
        raise HTTPException(
            status_code=403,
            detail=str(e),
        ) from e

    if result in (None, True):
        return
    if result is False:
        raise HTTPException(403, "Forbidden")

    if not isinstance(result, dict):
        raise HTTPException(
            500,
            f"Auth handler returned invalid result. Expected filter dict, None, or boolean. Got {type(result)} instead.",
        )

    return result


class CustomAuthBackend(AuthenticationBackend):
    def __init__(
        self,
        fn: Callable[
            [Request],
            Awaitable[tuple[list[str], Any]],
        ],
        disable_studio_auth: bool = False,
    ):
        if not inspect.iscoroutinefunction(fn):
            self.fn = functools.partial(run_in_threadpool, fn)
        else:
            self.fn = fn
        self._param_names = (
            _get_named_arguments(fn, supported_params=SUPPORTED_PARAMETERS)
            if fn
            else None
        )
        self.ls_auth = None
        if not disable_studio_auth:
            if LANGGRAPH_AUTH_TYPE == "langsmith":
                self.ls_auth = LangsmithAuthBackend()
            elif (
                LANGGRAPH_AUTH_TYPE == "noop"
                and (auth_type := os.environ.get("LANGSMITH_LANGGRAPH_API_VARIANT"))
                and auth_type == "local_dev"
            ):
                self.ls_auth = StudioNoopAuthBackend()

    def __str__(self):
        return (
            f"CustomAuthBackend(fn={self.fn}, "
            f"ls_auth={self.ls_auth}, "
            f"param_names={self._param_names}"
            ")"
        )

    async def authenticate(
        self, conn: HTTPConnection
    ) -> tuple[AuthCredentials, BaseUser] | None:
        if self.ls_auth is not None and (
            (auth_scheme := conn.headers.get("x-auth-scheme"))
            and auth_scheme == "langsmith"
        ):
            return await self.ls_auth.authenticate(conn)
        if self.fn is None:
            return None
        try:
            # ``starlette.requests.Request`` asserts ``scope["type"] ==
            # "http"`` in its constructor, so constructing one from a
            # WebSocket scope raises ``AssertionError``. Only build it
            # when the handler actually declared a ``request`` parameter
            # AND the scope is HTTP; reject WebSocket scopes with a
            # clear auth-layer error when the handler asks for one.
            request_obj: Request | None = None
            if self._param_names and "request" in self._param_names:
                if conn.scope.get("type") == "http":
                    request_obj = Request(conn.scope)
                else:
                    raise AuthError(
                        "Custom auth handler declares `request: Request`, "
                        "which is not supported for WebSocket connections. "
                        "Use `headers`, `authorization`, `scope`, or "
                        "`scopes` parameters instead.",
                        status_code=403,
                    )
            args = _extract_arguments_from_scope(
                conn.scope, self._param_names, request=request_obj
            )
            response = await self.fn(**args)
            return _normalize_auth_response(response)
        except (AuthenticationError, HTTPException):
            raise
        except Auth.exceptions.HTTPException as e:
            if e.status_code == 401 or e.status_code == 403:
                raise AuthError(str(e.detail), status_code=e.status_code) from None
            else:
                await logger.aerror("Error authenticating request", exc_info=e)
                raise
        except Exception as e:
            await logger.aerror("Error authenticating request", exc_info=e)
            raise e


def _get_custom_auth_middleware(
    config: str | dict,
) -> AuthenticationBackend:
    disable_studio_auth = False
    if isinstance(config, str):
        path: str | None = config
    else:
        path = config.get("path")
        disable_studio_auth = config.get("disable_studio_auth", disable_studio_auth)

    auth_instance = _get_auth_instance(path)
    if auth_instance is None:
        raise ValueError(
            f"Custom Auth object not found at path: {path}. "
            "Check that the path is correct and the file is available."
            "Auth objects are created like:\n"
            "from langgraph_sdk import Auth\n"
            "auth = Auth()"
        )

    if auth_instance == "js":
        from langgraph_api.js.remote import CustomJsAuthBackend  # noqa: PLC0415

        return CustomJsAuthBackend(disable_studio_auth=disable_studio_auth)

    if auth_instance._authenticate_handler is None:
        raise ValueError(
            f"Custom Auth object at path: {path} does not have an authenticate handler."
            "Please define one like:\n"
            "from langgraph_sdk import Auth\n"
            "auth = Auth()\n"
            "@auth.authenticate\n"
            "async def authenticate(request):\n"
            '    return "my-user-id"'
        )

    # We are wiring custom auth into the server middleware stack — emit the
    # missing-handler warning here (once at startup) rather than inside
    # `_get_auth_instance`, which is also reached from per-request dispatch
    # and from telemetry helpers that have no opinion about coverage.
    _warn_on_missing_handlers(auth_instance)

    result = CustomAuthBackend(
        auth_instance._authenticate_handler,
        disable_studio_auth,
    )
    logger.info(f"Loaded custom auth middleware: {result!s}")
    return result


@functools.lru_cache(maxsize=1)
def _get_auth_instance(path: str | None = None) -> Auth | Literal["js"] | None:
    auth_instance = _load_auth_obj(path) if path is not None else None

    if auth_instance == "js":
        return auth_instance
    deps = None
    if auth_instance is not None and (
        deps := _get_dependencies(auth_instance._authenticate_handler)
    ):
        auth_instance._authenticate_handler = _solve_fastapi_dependencies(
            auth_instance._authenticate_handler,
            deps,
        )
    logger.info(f"Loaded auth instance from path {path}: {auth_instance}")
    return auth_instance


def _derive_known_resource_actions(auth: Auth) -> dict[str, frozenset[str]]:
    """Walk an ``Auth.on`` namespace to discover every (resource, action) pair
    the SDK supports for handler registration.

    The SDK's ``_On`` class declares ``__slots__`` listing top-level resource
    namespaces (``assistants``, ``threads``, ``crons``, ``store``). Inside
    each namespace, the SDK installs one ``_ResourceActionOn`` /
    ``_StoreActionOn`` instance per supported action, each carrying a
    ``.action`` string attribute (and a ``.resource`` for the non-store ones).
    Duck-typing on those attributes avoids a hand-maintained list and keeps
    this in sync as the SDK adds resources/actions.
    """
    on = auth.on
    matrix: dict[str, set[str]] = {}
    for slot in getattr(type(on), "__slots__", ()) or ():
        if slot.startswith("_") or slot == "value":
            continue
        namespace = getattr(on, slot, None)
        if namespace is None:
            continue
        for attr_name in dir(namespace):
            if attr_name.startswith("_"):
                continue
            attr = getattr(namespace, attr_name, None)
            action = getattr(attr, "action", None)
            if not isinstance(action, str):
                continue
            # `_StoreActionOn` doesn't carry `.resource` (it's always
            # "store"); fall back to the outer namespace name.
            resource = getattr(attr, "resource", slot)
            if not isinstance(resource, str):
                continue
            matrix.setdefault(resource, set()).add(action)
    return {resource: frozenset(actions) for resource, actions in matrix.items()}


def _missing_handler_paths(auth: Auth) -> list[tuple[str, str]]:
    """Return ``(resource, action)`` pairs with no matching authorization handler.

    Mirrors the dispatch precedence in :func:`_get_handler`:
    - specific ``(resource, action)``
    - resource wildcard ``(resource, "*")``
    - action wildcard ``("*", action)``
    - full wildcard ``("*", "*")`` registered via ``auth.on(resources=["*"], actions=["*"])``
    - global ``@auth.on``

    Either of the last two covers everything, so an empty list is returned
    in those cases. Otherwise the returned list enumerates every
    resource/action pair the server may dispatch which would currently fall
    through to "allow".
    """
    if auth._global_handlers:
        return []
    handlers = auth._handlers
    # `auth.on(resources=["*"], actions=["*"])` stores under ("*", "*") in
    # `_handlers` (not `_global_handlers`) and `_get_handler` matches it as
    # the final fallback before `_global_handlers`. Treat it as full coverage.
    if ("*", "*") in handlers:
        return []
    missing: list[tuple[str, str]] = []
    for resource, actions in _derive_known_resource_actions(auth).items():
        for action in actions:
            if (resource, action) in handlers:
                continue
            if (resource, "*") in handlers:
                continue
            if ("*", action) in handlers:
                continue
            missing.append((resource, action))
    return missing


def _warn_on_missing_handlers(auth: Auth) -> None:
    """Log a warning when the user has registered some auth handlers but
    left other dispatch paths uncovered.

    Custom auth currently fails open: a request whose ``(resource, action)``
    has no matching handler is allowed without filtering. This is a common
    source of cross-user authorization bugs (see GHSA-38q2-qc5v-fccw and
    related issues), so we surface a warning at startup listing every
    uncovered path and a suggested default-deny snippet.

    """
    if not auth._handlers and not auth._global_handlers:
        # Auth instance with only an `@auth.authenticate` handler — the user
        # hasn't opted into resource-level authorization at all, so warning
        # about every path would be too noisy. The auth dispatch is a no-op
        # in this case.
        return
    missing = _missing_handler_paths(auth)
    if not missing:
        return
    paths = sorted(f"{resource}.{action}" for resource, action in missing)
    logger.warning(
        "Custom auth has no handler registered for some dispatch paths. "
        "Authentication (@auth.authenticate) still applies, but requests "
        "to these paths will be allowed without resource-level authorization "
        "checks (a common source of cross-user data leaks).",
        missing_paths=paths,
        suggestion=(
            "Add a default-deny global handler and override per-resource "
            "as needed:\n"
            "    @auth.on\n"
            "    async def deny_all(ctx, value):\n"
            "        return False"
        ),
    )


def _extract_arguments_from_scope(
    scope: Mapping[str, Any],
    param_names: set[str],
    request: Request | None = None,
    response: Response | None = None,
) -> dict[str, Any]:
    """Extract requested arguments from the ASGI scope (and request/response if needed)."""

    auth = scope.get("auth")
    args: dict[str, Any] = {}
    if "scope" in param_names:
        args["scope"] = scope
    if "request" in param_names and request is not None:
        args["request"] = request
    if "response" in param_names and response is not None:
        args["response"] = response
    if "user" in param_names:
        user = scope.get("user")
        args["user"] = user
    if "scopes" in param_names:
        args["scopes"] = auth.scopes if auth else []
    if "path_params" in param_names:
        args["path_params"] = scope.get("path_params", {})
    if "path" in param_names:
        args["path"] = scope["path"]
    if "query_params" in param_names:
        query_params = scope.get("query_string")
        if query_params:
            args["query_params"] = QueryParams(query_params)
        else:
            args["query_params"] = QueryParams()
    if "headers" in param_names:
        args["headers"] = dict(scope.get("headers", {}))
    if "authorization" in param_names:
        headers = dict(scope.get("headers", {}))
        authorization = headers.get(b"authorization") or headers.get(b"Authorization")
        if isinstance(authorization, bytes):
            authorization = authorization.decode(encoding="utf-8")
        args["authorization"] = authorization
    if "method" in param_names:
        args["method"] = scope.get("method")

    return args


def _get_dependencies(fn: Callable | None) -> dict[str, Any] | None:
    if fn is None:
        return None
    Depends = _depends()
    if Depends is None:
        # FastAPI not installed
        return

    # For Python versions < 3.10, get_annotations is available via inspect
    # For Python 3.10+, it's built-in. Here we just call it.
    annotations = (
        inspect.get_annotations(fn)
        if hasattr(inspect, "get_annotations")
        else getattr(fn, "__annotations__", {})
    )
    deps = {}
    for arg_name, arg_type in annotations.items():
        for annotation in get_args(arg_type):
            if isinstance(annotation, Depends):
                deps[arg_name] = annotation
                break
    return deps


def _solve_fastapi_dependencies(
    fn: Callable[..., Any], deps: Mapping[str, Any]
) -> Callable:
    """Solve FastAPI dependencies for a given function."""
    logger.info("Solving FastAPI dependencies", fn=str(fn), deps=str(deps))
    from fastapi.dependencies.utils import (  # noqa: PLC0415
        get_parameterless_sub_dependant,
        solve_dependencies,
    )

    dependents = {
        name: get_parameterless_sub_dependant(depends=dep, path="")
        for name, dep in deps.items()
    }
    for name, dependent in dependents.items():
        if dependent.call is None:
            raise ValueError(
                f"FastAPI-defined dependencies must have a callable dependency. No dependency found for {name} in {fn}."
            )

    is_async = inspect.iscoroutinefunction(fn)

    _param_names = {
        k
        for k in _get_named_arguments(
            fn, supported_params=SUPPORTED_PARAMETERS | dict(deps)
        )
        if k not in dependents
    }

    async def decorator(scope: dict, request: Request):
        async with AsyncExitStack() as request_stack:
            scope["fastapi_inner_astack"] = request_stack
            async with AsyncExitStack() as stack:
                scope["fastapi_function_astack"] = stack
                all_solved = await asyncio.gather(
                    *(
                        solve_dependencies(
                            request=request,
                            dependant=dependent,
                            async_exit_stack=stack,
                            embed_body_fields=False,
                        )
                        for dependent in dependents.values()
                    )
                )
                all_injected = await asyncio.gather(
                    *(
                        _run_async(dependent.call, solved.values, is_async)
                        for dependent, solved in zip(
                            dependents.values(), all_solved, strict=False
                        )
                    )
                )
                kwargs = {
                    name: value
                    for name, value in zip(
                        dependents.keys(), all_injected, strict=False
                    )
                }
                other_params = _extract_arguments_from_scope(
                    scope, _param_names, request=request
                )
                return await fn(**(kwargs | other_params))

    return decorator


@functools.lru_cache(maxsize=1)
def _depends() -> Any:
    if "fastapi" not in sys.modules:
        return None
    try:
        from fastapi.params import Depends  # noqa: PLC0415

        return Depends
    except ImportError:
        return None


_EXCLUDED = ("values", "keys", "items", "dict")


class DotDict:
    def __init__(self, dictionary: dict[str, Any]):
        self._dict = dictionary
        for key, value in dictionary.items():
            if isinstance(value, dict):
                setattr(self, key, DotDict(value))
            elif key not in _EXCLUDED:
                setattr(self, key, value)

    def __getattr__(self, name):
        if name not in self._dict:
            raise AttributeError(f"'DotDict' object has no attribute '{name}'")
        return self._dict[name]

    def __contains__(self, key: str) -> bool:
        return key in self._dict

    def __getitem__(self, key):
        return self._dict[key]

    def __setitem__(self, key, value):
        self._dict[key] = value
        if isinstance(value, dict):
            setattr(self, key, DotDict(value))
        else:
            setattr(self, key, value)

    def __deepcopy__(self, memo):
        return DotDict(copy.deepcopy(self._dict))

    def dict(self):
        return self._dict

    def items(self):
        return self._dict.items()

    def get(self, key, /, default=None):
        return self._dict.get(key, default)

    def values(self):
        return self._dict.values()

    def keys(self):
        return self._dict.keys()

    def __iter__(self):
        return iter(self._dict)

    def __len__(self):
        return len(self._dict)

    def __reduce__(self):
        return (self.__class__, (self._dict,))


class ProxyUser(BaseUser):
    """A proxy that wraps a user object to ensure it has all BaseUser properties.

    This will:
    1. Ensure the required identity property exists
    2. Provide defaults for optional properties if they don't exist
    3. Proxy all other attributes to the underlying user object
    """

    def __init__(self, user: Any):
        if not hasattr(user, "identity"):
            raise ValueError("User must have an identity property")
        self._user = user

    @property
    def identity(self) -> str:
        return self._user.identity

    @property
    def is_authenticated(self) -> bool:
        return getattr(self._user, "is_authenticated", True)

    @property
    def display_name(self) -> str:
        return getattr(self._user, "display_name", self.identity)

    def __deepcopy__(self, memo):
        return ProxyUser(copy.deepcopy(self._user))

    def model_dump(self):
        if hasattr(self._user, "model_dump") and callable(self._user.model_dump):
            return {
                "identity": self.identity,
                "is_authenticated": self.is_authenticated,
                "display_name": self.display_name,
                **self._user.model_dump(mode="json"),
            }
        return self.dict()

    def dict(self):
        d = (
            self._user.dict()
            if hasattr(self._user, "dict") and callable(self._user.dict)
            else {}
        )
        return {
            "identity": self.identity,
            "is_authenticated": self.is_authenticated,
            "display_name": self.display_name,
            **d,
        }

    def __contains__(self, key: str) -> bool:
        return key in self.dict()

    def __getitem__(self, key):
        return self.dict()[key]

    def __setitem__(self, key, value):
        self._user[key] = value

    def __getattr__(self, name: str) -> Any:
        """Proxy any other attributes to the underlying user object."""
        return getattr(self._user, name)

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

    def __str__(self) -> str:
        return f"{self._user}"

    def __reduce__(self):
        # Only store the wrapped user
        return (self.__class__, (self._user,))


class SimpleUser(ProxyUser):
    def __init__(self, username: str):
        super().__init__(DotDict({"identity": username}))


def _normalize_auth_response(
    response: Any,
) -> tuple[AuthCredentials, BaseUser]:
    if isinstance(response, tuple):
        if len(response) != 2:
            raise ValueError(
                f"Expected a tuple with two elements (permissions, user), got {len(response)}"
            )
        permissions, user = response
    elif hasattr(response, "permissions"):
        permissions = response.permissions
        user = response
    elif isinstance(response, dict | Mapping) and "permissions" in response:
        permissions = response["permissions"]
        user = response
    else:
        user = response
        permissions = []

    return AuthCredentials(permissions), normalize_user(user)


def normalize_user(user: Any) -> BaseUser:
    """Normalize user into a BaseUser instance."""
    if isinstance(user, BaseUser):
        return user
    if hasattr(user, "identity"):
        return ProxyUser(user)
    if isinstance(user, str):
        return SimpleUser(username=user)
    if isinstance(user, dict) and "identity" in user:
        return ProxyUser(DotDict(user))
    raise ValueError(
        f"Expected a BaseUser instance with required property: identity (str). "
        f"Optional properties are: is_authenticated (bool, defaults to True) and "
        f"display_name (str, defaults to identity). Got {type(user)} instead"
    )


@timing.timer(
    message="Loading custom auth {auth_path}",
    metadata_fn=lambda auth_path: {"auth_path": auth_path},
    warn_threshold_secs=5,
    warn_message="Loading custom auth '{auth_path}' took longer than expected",
    error_threshold_secs=10,
)
def _load_auth_obj(path: str) -> Auth | Literal["js"]:
    """Load an object from a path string."""
    if ":" not in path:
        raise ValueError(
            f"Invalid auth path format: {path}. "
            "Must be in format: './path/to/file.py:name' or 'module:name'"
        )

    module_name, callable_name = path.rsplit(":", 1)
    module_name = module_name.rstrip(":")

    if is_js_path(module_name):
        return "js"

    try:
        with profiled_import(path):
            if "/" in module_name or ".py" in module_name:
                # Load from file path
                modname = f"dynamic_module_{hash(module_name)}"
                modspec = importlib.util.spec_from_file_location(modname, module_name)
                if modspec is None or modspec.loader is None:
                    raise ValueError(f"Could not load file: {module_name}")
                module = importlib.util.module_from_spec(modspec)
                sys.modules[modname] = module
                modspec.loader.exec_module(module)
            else:
                # Load from Python module
                module = importlib.import_module(module_name)

        loaded_auth = getattr(module, callable_name, None)
        if loaded_auth is None:
            raise ValueError(
                f"Could not find auth '{callable_name}' in module: {module_name}"
            )
        if not isinstance(loaded_auth, Auth):
            raise ValueError(f"Expected an Auth instance, got {type(loaded_auth)}")
        return loaded_auth

    except ImportError as e:
        e.add_note(f"Could not import module:\n{module_name}\n\n")
        if os.environ.get("LANGSMITH_LANGGRAPH_API_VARIANT") == "local_dev":
            e.add_note(
                "If you're in development mode, make sure you've installed your project "
                "and its dependencies:\n"
                "- For requirements.txt: pip install -r requirements.txt\n"
                "- For pyproject.toml: pip install -e .\n"
            )
        raise
    except FileNotFoundError as e:
        raise ValueError(f"Could not find file: {module_name}") from e


async def _run_async(
    dep_call: Callable[..., Any], values: dict[str, Any], is_coroutine: bool
) -> Any:
    """Run a dependency call either in threadpool or directly if async."""
    if is_coroutine:
        return await dep_call(**values)
    return await run_in_threadpool(dep_call, **values)


def _get_handler(auth: Auth, ctx: Auth.types.AuthContext) -> Auth.types.Handler | None:
    """Get the most specific handler for a resource and action."""
    key = (ctx.resource, ctx.action)
    if key in auth._handler_cache:
        return auth._handler_cache[key]
    keys = [
        (ctx.resource, ctx.action),  # most specific
        (ctx.resource, "*"),  # resource-specific
        ("*", ctx.action),  # action-specific
        ("*", "*"),  # most general
    ]
    for key in keys:
        if key in auth._handlers:
            # Get the last defined, most specific handler
            result = auth._handlers[key][-1]
            auth._handler_cache[key] = result
            return result
    if auth._global_handlers:
        return auth._global_handlers[-1]

    return None


class StudioNoopAuthBackend(AuthenticationBackend):
    async def authenticate(
        self, conn: HTTPConnection
    ) -> tuple[AuthCredentials, BaseUser] | None:
        return AuthCredentials(), StudioUser("langgraph-studio-user")


def _get_named_arguments(fn: Callable, supported_params: dict) -> set[str]:
    """Get the named arguments that a function accepts, ensuring they're supported."""
    sig = inspect.signature(fn)
    # Check for unsupported required parameters
    unsupported = []
    for name, param in sig.parameters.items():
        if name not in supported_params and param.default is param.empty:
            unsupported.append(name)

    if unsupported:
        supported_str = "\n".join(
            f"  - {name} ({getattr(typ, '__name__', str(typ))})"
            for name, typ in supported_params.items()
        )
        raise ValueError(
            f"Handler has unsupported required parameters: {', '.join(unsupported)}.\n"
            f"Supported parameters are:\n{supported_str}"
        )

    return {p for p in sig.parameters if p in supported_params}
