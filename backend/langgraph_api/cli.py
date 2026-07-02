import argparse
import contextlib
import inspect
import json
import logging
import os
import pathlib
import socket
import threading
import time
import typing
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Literal

if typing.TYPE_CHECKING:
    from packaging.version import Version

    from langgraph_api.config import AuthConfig, HttpConfig, StoreConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


SUPPORT_STATUS = Literal["active", "critical", "eol"]


def _get_ls_origin() -> str | None:
    from langsmith.client import Client  # noqa: PLC0415
    from langsmith.utils import tracing_is_enabled  # noqa: PLC0415

    if not tracing_is_enabled():
        return
    client = Client()
    return client._host_url


def _get_org_id() -> str | None:
    from langsmith.client import Client  # noqa: PLC0415
    from langsmith.utils import tracing_is_enabled  # noqa: PLC0415

    # Yes, the organizationId is actually the workspace iD
    # which is actually the tenantID which we actually get via
    # the sessions endpoint
    if not tracing_is_enabled():
        return
    client = Client()
    try:
        response = client.request_with_retries(
            "GET", "/api/v1/sessions", params={"limit": 1}
        )
        result = response.json()
        if result:
            return result[0]["tenant_id"]
    except Exception as e:
        logger.debug("Failed to get organization ID: %s", str(e))
        return None


@contextlib.contextmanager
def patch_environment(**kwargs):
    """Temporarily patch environment variables.

    Args:
        **kwargs: Key-value pairs of environment variables to set.

    Yields:
        None
    """
    original = {}
    try:
        for key, value in kwargs.items():
            if value is None:
                original[key] = os.environ.pop(key, None)
                continue
            original[key] = os.environ.get(key)
            os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


DEFAULT_PORT = 2024


def _is_port_available(host: str, port: int) -> bool:
    """Check if a port is available for binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True
        except OSError:
            return False


def _find_open_port(host: str) -> int:
    """Find an available port by binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _resolve_server_url(
    host: str, port: int, *, mount_prefix: str | None, tunnel: bool
) -> str:
    """Return the public-facing base URL for the server.

    When *tunnel* is True a Cloudflare tunnel is started and the tunnel
    URL is returned; otherwise the local ``http://host:port`` URL is used.
    *mount_prefix* (if any) is appended to the result.
    """
    from langgraph_api.utils.network import format_hostport  # noqa: PLC0415

    upstream_url = f"http://{format_hostport(host, port)}"
    if mount_prefix:
        upstream_url += mount_prefix

    if not tunnel:
        return upstream_url

    logger.info("Starting Cloudflare Tunnel...")
    from langgraph_api.tunneling.cloudflare import start_tunnel  # noqa: PLC0415

    tunnel_obj = start_tunnel(port)
    try:
        public_url = tunnel_obj.url.result(timeout=30)
    except FutureTimeoutError:
        logger.warning(
            "Timed out waiting for Cloudflare Tunnel URL; using local URL %s",
            upstream_url,
        )
        public_url = upstream_url
    except Exception as e:
        tunnel_obj.process.kill()
        raise RuntimeError("Failed to start Cloudflare Tunnel") from e

    # Only append the prefix if we got a real tunnel URL; on timeout
    # fallback, public_url is already upstream_url which has the prefix.
    if mount_prefix and public_url != upstream_url:
        public_url += mount_prefix
    return public_url


def _resolve_port(host: str, port: int | None) -> int:
    """Resolve the port to use for the server."""
    requested = port if port is not None else DEFAULT_PORT
    if _is_port_available(host, requested):
        return requested
    found = _find_open_port(host)
    logger.warning(f"Port {requested} is already in use, using port {found} instead.")
    return found


def run_server(
    host: str = "127.0.0.1",
    port: int | None = None,
    reload: bool = False,
    graphs: dict | None = None,
    n_jobs_per_worker: int | None = None,
    env_file: str | None = None,
    open_browser: bool = False,
    tunnel: bool = False,
    debug_port: int | None = None,
    wait_for_client: bool = False,
    env: str | pathlib.Path | Mapping[str, str] | None = None,
    reload_includes: Sequence[str] | None = None,
    reload_excludes: Sequence[str] | None = None,
    store: typing.Optional["StoreConfig"] = None,
    auth: typing.Optional["AuthConfig"] = None,
    http: typing.Optional["HttpConfig"] = None,
    ui: dict | None = None,
    webhooks: dict | None = None,
    ui_config: dict | None = None,
    checkpointer: dict | None = None,
    studio_url: str | None = None,
    disable_persistence: bool = False,
    allow_blocking: bool = False,
    runtime_edition: Literal["inmem", "community", "postgres"] = "inmem",
    server_level: str = "WARNING",
    __redis_uri__: str | None = "fake",
    __database_uri__: str | None = ":memory:",
    __migrations_path__: str | None = "__inmem",
    **kwargs: typing.Any,
):
    """Run the LangGraph API server."""

    import uvicorn  # noqa: PLC0415

    port = _resolve_port(host, port)

    start_time = time.time()

    env_vars = env if isinstance(env, Mapping) else None
    mount_prefix = None
    if http is not None and http.get("mount_prefix") is not None:
        mount_prefix = http.get("mount_prefix")
    if os.environ.get("MOUNT_PREFIX"):
        mount_prefix = os.environ.get("MOUNT_PREFIX")
    if os.environ.get("LANGGRAPH_MOUNT_PREFIX"):
        mount_prefix = os.environ.get("LANGGRAPH_MOUNT_PREFIX")
    if isinstance(env, str | pathlib.Path):
        try:
            from dotenv.main import (  # noqa: PLC0415
                DotEnv,
            )

            env_vars = DotEnv(dotenv_path=env).dict() or {}
            logger.debug(f"Loaded environment variables from {env}: {sorted(env_vars)}")

        except ImportError:
            logger.warning(
                "python_dotenv is not installed. Environment variables will not be available."
            )

    if debug_port is not None:
        try:
            import debugpy  # noqa: PLC0415  # ty: ignore[unresolved-import]
        except ImportError:
            logger.warning("debugpy is not installed. Debugging will not be available.")
            logger.info("To enable debugging, install debugpy: pip install debugpy")
            return
        debugpy.listen((host, debug_port))
        logger.info(
            f"🐛 Debugger listening on port {debug_port}. Waiting for client to attach..."
        )
        logger.info("To attach the debugger:")
        logger.info("1. Open your python debugger client (e.g., Visual Studio Code).")
        logger.info(
            "2. Use the 'Remote Attach' configuration with the following settings:"
        )
        logger.info("   - Host: 0.0.0.0")
        logger.info(f"   - Port: {debug_port}")
        logger.info("3. Start the debugger to connect to the server.")
        if wait_for_client:
            debugpy.wait_for_client()
            logger.info("Debugger attached. Starting server...")

    # Build all env patches up front so that langgraph_api.config (which
    # is read at import time) sees every config value.  LANGGRAPH_API_URL
    # is resolved inside the block (it depends on tunnel/mount_prefix) and
    # overwritten then; the empty placeholder ensures patch_environment
    # tracks and restores the original value on exit.
    to_patch = dict(
        MIGRATIONS_PATH=__migrations_path__,
        DATABASE_URI=__database_uri__,
        REDIS_URI=__redis_uri__,
        N_JOBS_PER_WORKER=str(
            n_jobs_per_worker if n_jobs_per_worker is not None else 1
        ),
        LANGGRAPH_STORE=json.dumps(store) if store else None,
        LANGSERVE_GRAPHS=json.dumps(graphs) if graphs else None,
        LANGSMITH_LANGGRAPH_API_VARIANT="local_dev",
        LANGGRAPH_AUTH=json.dumps(auth) if auth else None,
        LANGGRAPH_HTTP=json.dumps(http) if http else None,
        LANGGRAPH_UI=json.dumps(ui) if ui else None,
        LANGGRAPH_WEBHOOKS=json.dumps(webhooks) if webhooks else None,
        LANGGRAPH_UI_CONFIG=json.dumps(ui_config) if ui_config else None,
        LANGGRAPH_CHECKPOINTER=json.dumps(checkpointer) if checkpointer else None,
        LANGGRAPH_UI_BUNDLER="true",
        LANGGRAPH_API_URL="",  # resolved below, inside the patched block
        LANGGRAPH_DISABLE_FILE_PERSISTENCE=str(disable_persistence).lower(),
        LANGGRAPH_RUNTIME_EDITION=runtime_edition,
        # If true, we will not raise on blocking IO calls (via blockbuster)
        LANGGRAPH_ALLOW_BLOCKING=str(allow_blocking).lower(),
        # See https://developer.chrome.com/blog/private-network-access-update-2024-03
        ALLOW_PRIVATE_NETWORK="true",
    )
    if env_vars is not None:
        # Don't overwrite.
        for k, v in env_vars.items():
            if k in to_patch:
                logger.debug(f"Skipping loaded env var {k}={v}")
                continue
            to_patch[k] = v

    with patch_environment(**to_patch):
        local_url = _resolve_server_url(
            host, port, mount_prefix=mount_prefix, tunnel=tunnel
        )
        os.environ["LANGGRAPH_API_URL"] = local_url

        studio_origin = studio_url or _get_ls_origin() or "https://smith.langchain.com"
        full_studio_url = f"{studio_origin}/studio/?baseUrl={local_url}"

        def _open_browser():
            nonlocal studio_origin, full_studio_url
            import webbrowser  # noqa: PLC0415

            thread_logger = logging.getLogger("browser_opener")
            if not thread_logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter("%(message)s"))
                thread_logger.addHandler(handler)

            with ThreadPoolExecutor(max_workers=1) as executor:
                org_id_future = executor.submit(_get_org_id)

                while True:
                    try:
                        with urllib.request.urlopen(f"{local_url}/ok") as response:
                            if response.status == 200:
                                try:
                                    org_id = org_id_future.result(timeout=3.0)
                                    if org_id:
                                        full_studio_url = f"{studio_origin}/studio/?baseUrl={local_url}&organizationId={org_id}"
                                except TimeoutError as e:
                                    thread_logger.debug(
                                        f"Failed to get organization ID: {e!s}"
                                    )
                                    pass
                                thread_logger.info(
                                    f"Server started in {time.time() - start_time:.2f}s"
                                )
                                thread_logger.info(
                                    "🎨 Opening Studio in your browser..."
                                )
                                thread_logger.info("URL: " + full_studio_url)
                                webbrowser.open(full_studio_url)
                                return
                    except urllib.error.URLError:
                        pass
                    time.sleep(0.1)

        welcome = f"""

        Welcome to

╦  ┌─┐┌┐┌┌─┐╔═╗┬─┐┌─┐┌─┐┬ ┬
║  ├─┤││││ ┬║ ╦├┬┘├─┤├─┘├─┤
╩═╝┴ ┴┘└┘└─┘╚═╝┴└─┴ ┴┴  ┴ ┴

- 🚀 API: \033[36m{local_url}\033[0m
- 🎨 Studio UI: \033[36m{full_studio_url}\033[0m
- 📚 API Docs: \033[36m{local_url}/docs\033[0m

This in-memory server is designed for development and testing.
For production use, please use LangSmith Deployment.

"""
        logger.info(welcome)
        if open_browser:
            threading.Thread(target=_open_browser, daemon=True).start()
        # Not in public docs: LANGGRAPH_NO_VERSION_CHECK is dev-only
        nvc = os.getenv("LANGGRAPH_NO_VERSION_CHECK")
        if nvc is None or nvc.lower() not in ("true", "1"):
            from langgraph_api import __version__  # noqa: PLC0415

            threading.Thread(
                target=_check_newer_version,
                args=("langgraph-api", __version__),
                daemon=True,
            ).start()
        supported_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k in inspect.signature(uvicorn.run).parameters
        }
        server_level = server_level.upper()
        uvicorn.run(
            "langgraph_api.server:app",
            host=host,
            port=port,
            reload=reload,
            env_file=env_file,
            access_log=False,
            reload_includes=list(reload_includes) if reload_includes else None,
            reload_excludes=list(reload_excludes) if reload_excludes else None,
            log_config={
                "version": 1,
                "incremental": False,
                "disable_existing_loggers": False,
                "formatters": {
                    "simple": {
                        "class": "langgraph_api.logging.Formatter",
                    }
                },
                "handlers": {
                    "console": {
                        "class": "logging.StreamHandler",
                        "formatter": "simple",
                        "stream": "ext://sys.stdout",
                    }
                },
                "loggers": {
                    "uvicorn": {"level": server_level},
                    "uvicorn.error": {"level": server_level},
                    "langgraph_api.server": {"level": server_level},
                },
                "root": {"handlers": ["console"]},
            },
            **supported_kwargs,
        )


def main():
    parser = argparse.ArgumentParser(
        description="CLI entrypoint for running the LangGraph API server."
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host to bind the server to"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind the server to (default: 2024; auto-discovers another port if the requested one is in use)",
    )
    parser.add_argument("--no-reload", action="store_true", help="Disable auto-reload")
    parser.add_argument(
        "--config", default="langgraph.json", help="Path to configuration file"
    )
    parser.add_argument(
        "--n-jobs-per-worker",
        type=int,
        help="Number of jobs per worker. Default is None (meaning 10)",
    )
    parser.add_argument(
        "--open-browser", action="store_true", help="Open browser automatically"
    )
    parser.add_argument(
        "--debug-port", type=int, help="Port for debugger to listen on (default: none)"
    )
    parser.add_argument(
        "--wait-for-client",
        action="store_true",
        help="Whether to break and wait for a debugger to attach",
    )
    parser.add_argument(
        "--tunnel",
        action="store_true",
        help="Expose the server via Cloudflare Tunnel",
    )
    parser.add_argument(
        "--runtime-edition",
        type=str,
        default="inmem",
        help="Runtime edition to use",
    )
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config_data = json.load(f)

    graphs = config_data.get("graphs", {})
    auth = config_data.get("auth")
    ui = config_data.get("ui")
    webhooks = config_data.get("webhooks")
    ui_config = config_data.get("ui_config")
    checkpointer = config_data.get("checkpointer")
    kwargs = {}
    if args.runtime_edition == "postgres":
        kwargs["__redis_uri__"] = os.getenv("REDIS_URI")
        kwargs["__database_uri__"] = os.getenv("DATABASE_URI")
        kwargs["__migrations_path__"] = os.getenv("MIGRATIONS_PATH")
    run_server(
        args.host,
        args.port,
        not args.no_reload,
        graphs,
        n_jobs_per_worker=args.n_jobs_per_worker,
        open_browser=args.open_browser,
        tunnel=args.tunnel,
        debug_port=args.debug_port,
        wait_for_client=args.wait_for_client,
        env=config_data.get("env", None),
        auth=auth,
        ui=ui,
        webhooks=webhooks,
        ui_config=ui_config,
        checkpointer=checkpointer,
        runtime_edition=args.runtime_edition,
        **kwargs,
    )


def _check_newer_version(pkg: str, current_version: str, timeout: float = 0.5) -> None:
    """Check PyPI for newer versions and log support status.

    Critical = one minor behind on same major, OR latest minor of previous major while latest is X.0.*
    EOL = two+ minors behind on same major, OR any previous major after X.1.*
    """
    from packaging.version import InvalidVersion, Version  # noqa: PLC0415

    log = logging.getLogger("version_check")
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(h)

    if os.getenv("LANGGRAPH_NO_VERSION_CHECK", "").lower() in ("true", "1"):
        return

    def _parse(v: str) -> Version | None:
        try:
            return Version(v)
        except InvalidVersion:
            return None

    try:
        current = Version(current_version)
    except InvalidVersion:
        log.info(
            f"[version] Could not parse installed version {current_version!r}. Skipping support check."
        )
        return

    try:
        with urllib.request.urlopen(
            f"https://pypi.org/pypi/{pkg}/json", timeout=timeout
        ) as resp:
            payload = json.load(resp)
        latest_str = payload["info"]["version"]
        latest = Version(latest_str)
        releases: dict[str, list[dict]] = payload.get("releases", {})
    except Exception:
        log.debug("Failed to retrieve latest version info for %s", pkg)
        return
    prev_major_latest_minor: Version | None = None
    if latest.major > 0:
        pm = latest.major - 1
        prev_major_versions = [
            v
            for s in releases
            if (v := _parse(s)) is not None and not v.is_prerelease and v.major == pm
        ]
        if prev_major_versions:
            prev_major_latest_minor = max(
                prev_major_versions, key=lambda v: (v.major, v.minor, v.micro)
            )

    if latest > current and not current.is_prerelease:
        log.info(
            "[version] A newer version of %s is available: %s → %s  (pip install -U %s)",
            pkg,
            current,
            latest,
            pkg,
        )

    level = _support_level(current, latest, prev_major_latest_minor)
    changelog = (
        "https://docs.langchain.com/langgraph-platform/langgraph-server-changelog"
    )

    if level == "critical":
        # Distinguish same-major vs cross-major grace in the wording
        if current.major == latest.major and current.minor == latest.minor - 1:
            tail = "You are one minor version behind the latest (%d.%d.x).\n"
        else:
            tail = "You are on the latest minor of the previous major while a new major (%d.%d.x) just released.\n"
        log.info(
            "⚠️ [support] %s %s is in Critical support.\n"
            "Only critical security and installation fixes are provided.\n"
            + tail
            + "Please plan an upgrade soon. See changelog: %s",
            pkg,
            current,
            latest.major,
            latest.minor,
            changelog,
        )
    elif level == "eol":
        log.info(
            "⚠️ [support] %s %s is End of Life.\n"
            "No bug fixes or security updates will be provided.\n"
            "You are two or more minor versions behind the latest (%d.%d.x).\n"
            "You should upgrade immediately. See changelog: %s",
            pkg,
            current,
            latest.major,
            latest.minor,
            changelog,
        )


def _support_level(
    cur: "Version", lat: "Version", prev_major_latest_minor: "Version | None"
) -> SUPPORT_STATUS:
    if cur.major > lat.major:
        return "active"
    if cur.major == lat.major:
        if cur.minor == lat.minor:
            return "active"
        if cur.minor == lat.minor - 1:
            return "critical"
        if cur.minor <= lat.minor - 2:
            return "eol"
        return "active"

    if cur.major == lat.major - 1 and lat.minor == 0:
        if (
            prev_major_latest_minor is not None
            and cur.minor == prev_major_latest_minor.minor
        ):
            return "critical"
        return "eol"

    return "eol"


if __name__ == "__main__":
    main()
