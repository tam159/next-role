"""Basic distributed key/value cache for internal use."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Generic, Literal, TypedDict, TypeVar, cast

import orjson

from langgraph_api.feature_flags import IS_POSTGRES_OR_GRPC_BACKEND
from langgraph_api.utils.cache import LRUCache

MAX_CACHE_TTL = timedelta(hours=24)

if IS_POSTGRES_OR_GRPC_BACKEND:
    from langgraph_api.grpc.ops.cache import cache_get as _cache_get
    from langgraph_api.grpc.ops.cache import cache_set as _cache_set
else:
    _CACHE: LRUCache[bytes] = LRUCache(ttl=MAX_CACHE_TTL.total_seconds())

    async def _cache_get(key: str) -> bytes | None:
        return await _CACHE.get(key)

    async def _cache_set(key: str, value: bytes, ttl: timedelta | None = None) -> None:
        ttl = _clamp_ttl(ttl)
        _CACHE.set(key, value)


logger = logging.getLogger(__name__)

_SWR_KEY_PREFIX = "__lg_swr__:"
_SWR_SCHEMA_VERSION = 1
_DELTA_ZERO = timedelta(0)
_UNSET: Any = object()


JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None

CacheStatus = Literal["miss", "fresh", "stale", "expired"]

T = TypeVar("T")

__all__ = ["CacheStatus", "JsonValue", "SWRResult", "cache_get", "cache_set", "swr"]


class SWRResult(Generic[T]):
    """Result wrapper returned by :func:`swr`.

    Attributes:
        value: The cached (or freshly loaded) value.
        status: How the value was resolved: ``"miss"``, ``"fresh"``,
            ``"stale"``, or ``"expired"``.
    """

    __slots__ = ("_cache_key", "_loader", "_max_age", "_model", "status", "value")

    def __init__(
        self,
        value: T,
        *,
        cache_key: str,
        loader: Callable[[], Awaitable[Any]],
        max_age: timedelta,
        status: CacheStatus,
        model: type[T] | None = None,
    ) -> None:
        self.value = value
        self._cache_key = cache_key
        self._loader = loader
        self._max_age = max_age
        self._model = model
        self.status: CacheStatus = status

    async def mutate(self, value: T = _UNSET) -> T:  # ty: ignore[invalid-parameter-default]
        """Update or revalidate the cached value.

        Args:
            value: If provided, optimistically write this value into the cache.
                If omitted, re-run the original loader to revalidate.

        Returns:
            The new cached value.
        """
        if value is _UNSET:
            raw = await _await_swr_load(self._cache_key, self._loader, self._max_age)
            result = (
                cast("T", self._model.model_validate(raw))
                if self._model is not None
                else cast("T", raw)
            )
        else:
            raw = value.model_dump(mode="json") if self._model is not None else value
            await _write_swr_value(self._cache_key, raw, self._max_age)
            result = value
        self.value = result
        return result

    def __repr__(self) -> str:
        return f"SWRResult(value={self.value!r}, status={self.status!r})"


async def cache_get(key: str) -> Any | None:
    """Get a value from the cache."""
    val = await _cache_get(key)
    return orjson.loads(val) if val is not None else None


async def cache_set(key: str, value: Any, ttl: timedelta | None = None) -> None:
    """Set a value in the cache.

    Args:
        key: The cache key.
        value: The value to cache (must be serializable to JSON).
        ttl: Optional time-to-live.  Capped at MAX_CACHE_TTL (24 hours by default);
            `None` or zero defaults to MAX_CACHE_TTL.
    """
    ttl = _clamp_ttl(ttl)
    await _cache_set(key, orjson.dumps(value), ttl)


class _SWREntry(TypedDict):
    v: int
    value: Any
    stored_at_ms: int


@dataclass(slots=True)
class _SWRState:
    task: asyncio.Task[Any] | None = None
    write_epoch: int = 0
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


_SWR_STATES: dict[str, _SWRState] = {}


async def swr(
    key: str,
    loader: Callable[[], Awaitable[T]],
    *,
    fresh_for: timedelta = _DELTA_ZERO,
    max_age: timedelta | None = None,
    model: type[T] | None = None,
) -> SWRResult[T]:
    """Load a cached value using stale-while-revalidate semantics.

    This helper is server-side only and is intended for caching internal async
    dependencies such as auth or metadata lookups.

    Args:
        key: Cache key.
        loader: Async callable that fetches the value on miss/revalidation.
        fresh_for: How long a cached value is considered fresh (no revalidation).
            Defaults to ``timedelta(0)`` so every access triggers a background
            revalidate while still returning the cached value instantly. Values
            above :data:`MAX_CACHE_TTL` are clamped to the backend maximum.
        max_age: Total lifetime of a cached entry. After this, the next access
            blocks on the loader. Defaults to :data:`MAX_CACHE_TTL` (24 h by
            default). Values above :data:`MAX_CACHE_TTL` are clamped to the
            backend maximum.
        model: Optional Pydantic model class. When provided, values are
            serialized via ``model_dump(mode="json")`` before storage and
            deserialized via ``model.model_validate()`` on read.

    Returns:
        An :class:`SWRResult` with ``.value``, ``.status``, and an async
        ``.mutate()`` method.

    Semantics:
    - cache miss: await ``loader()``, store the value, return it
    - fresh hit (age < fresh_for): return the cached value
    - stale hit (fresh_for <= age < max_age): return the cached value
      immediately and trigger a best-effort background refresh
    - expired (age >= max_age): await ``loader()``, store the value, return it
    """
    resolved_fresh_for = min(fresh_for, MAX_CACHE_TTL)
    resolved_max_age = _resolve_swr_max_age(max_age)
    fresh_for_ms, max_age_ms, resolved_max_age = _validate_swr_windows(
        resolved_fresh_for, resolved_max_age
    )
    cache_key = _swr_cache_key(key)

    # When a pydantic model is provided, wrap the loader to serialize before
    # storage and deserialize after reads.  The internal helpers always deal
    # with plain JSON-compatible values.
    if model is not None and hasattr(model, "model_validate"):
        _original_loader = loader

        async def _json_loader() -> Any:
            val = await _original_loader()
            return val.model_dump(mode="json")

        json_loader: Callable[[], Awaitable[Any]] = _json_loader

        def _deserialize(raw: Any) -> T:
            return cast("T", model.model_validate(raw))  # ty: ignore[call-non-callable]
    else:
        json_loader = loader

        def _deserialize(raw: Any) -> T:
            return cast("T", raw)

    def _result(value: T, status: CacheStatus) -> SWRResult[T]:
        return SWRResult(
            value,
            cache_key=cache_key,
            loader=json_loader,
            max_age=resolved_max_age,
            status=status,
            model=model,
        )

    entry = _parse_swr_entry(await cache_get(cache_key))
    if entry is None:
        raw = await _await_swr_load(cache_key, json_loader, resolved_max_age)
        return _result(_deserialize(raw), "miss")

    age_ms = max(0, _now_ms() - entry["stored_at_ms"])
    cached_value = _deserialize(entry["value"])

    if age_ms < fresh_for_ms:
        return _result(cached_value, "fresh")

    if age_ms < max_age_ms:
        _start_swr_refresh(cache_key, json_loader, resolved_max_age)
        return _result(cached_value, "stale")

    raw = await _await_swr_load(cache_key, json_loader, resolved_max_age)
    return _result(_deserialize(raw), "expired")


def _clamp_ttl(ttl: timedelta | None) -> timedelta:
    """Normalise caller-supplied TTL: None/0 -> MAX, >MAX -> MAX."""
    if ttl is None or ttl <= timedelta(0):
        return MAX_CACHE_TTL
    return min(ttl, MAX_CACHE_TTL)


def _resolve_swr_max_age(max_age: timedelta | None) -> timedelta:
    if max_age is None:
        return MAX_CACHE_TTL
    if max_age <= _DELTA_ZERO:
        return max_age
    return min(max_age, MAX_CACHE_TTL)


def _swr_cache_key(key: str) -> str:
    return f"{_SWR_KEY_PREFIX}{key}"


def _validate_swr_windows(
    fresh_for: timedelta, max_age: timedelta
) -> tuple[int, int, timedelta]:
    if fresh_for < _DELTA_ZERO:
        raise ValueError("fresh_for must be >= 0")
    if max_age <= _DELTA_ZERO:
        raise ValueError("max_age must be > 0")
    if fresh_for > max_age:
        raise ValueError("fresh_for must be <= max_age")

    return (
        int(fresh_for.total_seconds() * 1000),
        int(max_age.total_seconds() * 1000),
        max_age,
    )


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _parse_swr_entry(value: Any) -> _SWREntry | None:
    if not isinstance(value, dict):
        return None

    version = value.get("v")
    stored_at_ms = value.get("stored_at_ms")
    if version != _SWR_SCHEMA_VERSION or not isinstance(stored_at_ms, int):
        return None
    if "value" not in value:
        return None

    return {
        "v": _SWR_SCHEMA_VERSION,
        "value": value["value"],
        "stored_at_ms": stored_at_ms,
    }


def _acquire_swr_state(cache_key: str) -> _SWRState:
    state = _SWR_STATES.get(cache_key)
    if state is None:
        state = _SWRState()
        _SWR_STATES[cache_key] = state
    state.users += 1
    return state


def _release_swr_state(cache_key: str, state: _SWRState) -> None:
    state.users -= 1
    _maybe_cleanup_swr_state(cache_key, state)


def _maybe_cleanup_swr_state(cache_key: str, state: _SWRState) -> None:
    if (
        state.users == 0
        and state.task is None
        and not state.write_lock.locked()
        and _SWR_STATES.get(cache_key) is state
    ):
        _SWR_STATES.pop(cache_key, None)


async def _cache_swr_entry(cache_key: str, value: Any, ttl: timedelta) -> None:
    entry: _SWREntry = {
        "v": _SWR_SCHEMA_VERSION,
        "value": value,
        "stored_at_ms": _now_ms(),
    }
    await cache_set(cache_key, entry, ttl=ttl)


async def _write_swr_value(cache_key: str, value: Any, ttl: timedelta) -> None:
    state = _acquire_swr_state(cache_key)
    try:
        async with state.write_lock:
            state.write_epoch += 1
            await _cache_swr_entry(cache_key, value, ttl)
    finally:
        _release_swr_state(cache_key, state)


async def _store_loaded_swr_value(
    cache_key: str, value: Any, ttl: timedelta, *, start_epoch: int
) -> bool:
    state = _acquire_swr_state(cache_key)
    try:
        async with state.write_lock:
            if start_epoch != state.write_epoch:
                return False
            await _cache_swr_entry(cache_key, value, ttl)
            return True
    finally:
        _release_swr_state(cache_key, state)


async def _load_and_store_swr_value(
    cache_key: str,
    loader: Callable[[], Awaitable[T]],
    ttl: timedelta,
    *,
    start_epoch: int,
) -> T:
    value = await loader()
    if await _store_loaded_swr_value(cache_key, value, ttl, start_epoch=start_epoch):
        return value

    entry = _parse_swr_entry(await cache_get(cache_key))
    if entry is not None:
        return cast("T", entry["value"])
    return value


def _ensure_swr_load_task(
    cache_key: str,
    loader: Callable[[], Awaitable[T]],
    ttl: timedelta,
    *,
    log_errors: bool,
) -> asyncio.Task[T]:
    state = _acquire_swr_state(cache_key)
    try:
        existing = state.task
        if existing is not None and not existing.done():
            return cast("asyncio.Task[T]", existing)

        task = asyncio.create_task(
            _load_and_store_swr_value(
                cache_key, loader, ttl, start_epoch=state.write_epoch
            )
        )
        state.task = task

        def _cleanup(done: asyncio.Task[Any]) -> None:
            cleanup_state = _SWR_STATES.get(cache_key)
            if cleanup_state is not None and cleanup_state.task is done:
                cleanup_state.task = None
                _maybe_cleanup_swr_state(cache_key, cleanup_state)
            try:
                done.result()
            except Exception:
                if log_errors:
                    logger.debug(
                        "Background swr refresh failed for %s",
                        cache_key,
                        exc_info=True,
                    )

        task.add_done_callback(_cleanup)
        return task
    finally:
        _release_swr_state(cache_key, state)


async def _await_swr_load(
    cache_key: str, loader: Callable[[], Awaitable[T]], ttl: timedelta
) -> T:
    return await _ensure_swr_load_task(cache_key, loader, ttl, log_errors=False)


def _start_swr_refresh(
    cache_key: str, loader: Callable[[], Awaitable[Any]], ttl: timedelta
) -> None:
    _ensure_swr_load_task(cache_key, loader, ttl, log_errors=True)
