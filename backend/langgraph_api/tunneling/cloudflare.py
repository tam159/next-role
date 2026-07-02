import atexit
import logging
import os
import platform as _platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request
from concurrent.futures import Future
from pathlib import Path

logger = logging.getLogger(__name__)

# Not in public docs: internal, controls cloudflared binary version for tunneling
CLOUDFLARED_VERSION = os.environ.get("LANGGRAPH_CLOUDFLARED_VERSION", "2025.2.1")
CACHE_DIR = (
    Path(os.path.expanduser("~"))
    / ".langgraph_api"
    / "cloudflared"
    / CLOUDFLARED_VERSION
)


def get_platform_arch():
    plat = sys.platform
    if plat.startswith("darwin"):
        plat = "darwin"
    elif plat.startswith("linux"):
        plat = "linux"
    elif plat.startswith("win"):
        plat = "windows"
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform}")
    arch = _platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        arch = "amd64"
    elif arch in ("arm64", "aarch64"):
        arch = "arm64"
    elif arch in ("i386", "i686", "x86"):
        arch = "386"
    else:
        raise RuntimeError(f"Unsupported architecture: {_platform.machine()}")
    return plat, arch


def ensure_cloudflared() -> Path:
    plat, arch = get_platform_arch()
    # determine file names and if archive
    if plat == "windows":
        file_name = f"cloudflared-windows-{arch}.exe"
        is_archive = False
    elif plat == "darwin":
        file_name = f"cloudflared-darwin-{arch}.tgz"
        is_archive = True
    else:  # linux
        file_name = f"cloudflared-linux-{arch}"
        is_archive = False

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    binary_name = "cloudflared" if is_archive else file_name
    target_bin = CACHE_DIR / binary_name
    if not target_bin.exists():
        url = f"https://github.com/cloudflare/cloudflared/releases/download/{CLOUDFLARED_VERSION}/{file_name}"
        logger.info(f"Downloading cloudflared from {url}")
        with urllib.request.urlopen(url) as resp:
            data = resp.read()
        with tempfile.TemporaryDirectory() as tmpd:
            tmpd = Path(tmpd)
            path = tmpd / file_name
            path.write_bytes(data)
            if is_archive:
                with tarfile.open(path) as tf:
                    safe_members = []
                    for member in tf.getmembers():
                        member_path = os.path.normpath(os.path.join(tmpd, member.name))
                        if not member_path.startswith(str(tmpd) + os.sep):
                            raise ValueError(
                                f"Tar member {member.name!r} escapes target directory"
                            )
                        safe_members.append(member)
                    tf.extractall(tmpd, members=safe_members)
                src = tmpd / "cloudflared"
            else:
                src = path
            shutil.move(str(src), str(target_bin))
        target_bin.chmod(0o755)
    return target_bin


class CloudflareTunnel:
    def __init__(self, process: subprocess.Popen, url_future: Future[str]):
        self.process = process
        self.url = url_future


def start_tunnel(port: int) -> CloudflareTunnel:
    bin_path = ensure_cloudflared()
    cmd = [str(bin_path), "tunnel", "--url", f"http://localhost:{port}"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    atexit.register(proc.kill)

    url_future: Future[str] = Future()

    def _reader(stream):
        for line in stream:
            line = line.strip()
            logger.info(f"[cloudflared] {line}")
            if not url_future.done():
                # match any trycloudflare.com host with optional subdomains
                pattern = re.compile(r"(https://[A-Za-z0-9.-]+\.trycloudflare\.com)")
                m = pattern.search(line)
                if m:
                    url_future.set_result(m.group(1))

    threading.Thread(target=_reader, args=(proc.stdout,), daemon=True).start()
    threading.Thread(target=_reader, args=(proc.stderr,), daemon=True).start()

    return CloudflareTunnel(proc, url_future)
