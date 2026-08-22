# NextRole sandbox template image.
#
# Built into a CubeSandbox template ON THE SANDBOX HOST (see ../README.md) —
# never by docker compose. It defines the filesystem every `execute` command
# and every `rendercv render` runs in when SANDBOX_PROVIDER=e2b.
#
# Keep the rendercv pin in lockstep with backend/pyproject.toml. The
# rendercv[full] wheels bundle the Typst compiler and the rendercv font set,
# so no system fonts or typst package are needed. WeasyPrint is deliberately
# absent — the battlecard PDF renders in-process in the backend, never via
# `execute`.
FROM python:3.13-slim

# The deepagents file tools (ls/read/glob/grep/edit) shell out to python3;
# this base provides it plus POSIX coreutils. Console scripts (rendercv)
# land in /usr/local/bin, which is on PATH.
RUN pip install --no-cache-dir "rendercv[full]>=2.8"

# E2B template convention: commands run as `user` with a writable home
# (the backend's SANDBOX_CWD defaults to /home/user).
RUN useradd -m -u 1000 -s /bin/bash user
USER user
WORKDIR /home/user
