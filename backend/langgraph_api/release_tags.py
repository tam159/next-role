"""Helpers for computing publish image tags.

This module mirrors the tag generation logic from:
- `.github/workflows/publish.yml` (Python images)
- `.github/workflows/publish-js.yml` (JS images)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def _require_non_empty(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _normalize_projects(projects: Iterable[str]) -> list[str]:
    return [p.strip() for p in projects if p and p.strip()]


def _sorted_unique(tags: Iterable[str]) -> list[str]:
    return sorted({tag for tag in tags if tag})


def _release_channel_alias(channel: str) -> str:
    normalized = _require_non_empty("release_channel", channel).lower()
    if normalized not in {"stable", "rc", "latest"}:
        raise ValueError("release_channel must be one of: stable, rc, latest")
    return normalized


def _stable_or_channel_tag(
    *,
    is_stable_channel: bool,
    stable_tag: str,
    channel_pinned_alias: str,
) -> str:
    """Use legacy stable tag format, otherwise channel-scoped pin."""
    if is_stable_channel:
        return stable_tag
    return channel_pinned_alias


def _python_suffix(distro: str) -> str:
    if distro == "base":
        return ""
    if distro == "bookworm":
        return "-bookworm"
    if distro == "wolfi":
        return "-wolfi"
    raise ValueError(f"Unsupported distro: {distro}")


def compute_python_publish_tags(
    *,
    py_version: str,
    distro: str,
    licensed: bool,
    sha: str,
    ver_patch: str,
    ver_major: str,
    ver_minor: str,
    latest_on: bool,
    release_channel: str,
    test_mode: bool,
    gcr_projects: Iterable[str],
    ar_project: str,
    ecr_prefix: str,
    img_api_dh: str,
    img_svr_dh: str,
) -> list[str]:
    """Compute Python publish tags with workflow-compatible behavior."""
    py_version = _require_non_empty("py_version", py_version)
    distro = _require_non_empty("distro", distro)
    sha = _require_non_empty("sha", sha)
    ver_patch = _require_non_empty("ver_patch", ver_patch)
    ver_major = _require_non_empty("ver_major", ver_major)
    ver_minor = _require_non_empty("ver_minor", ver_minor)
    ar_project = _require_non_empty("ar_project", ar_project)
    ecr_prefix = _require_non_empty("ecr_prefix", ecr_prefix)
    img_api_dh = _require_non_empty("img_api_dh", img_api_dh)
    img_svr_dh = _require_non_empty("img_svr_dh", img_svr_dh)
    channel = _release_channel_alias(release_channel)

    projects = _normalize_projects(gcr_projects)
    if not projects:
        raise ValueError("gcr_projects must include at least one project")

    suf = _python_suffix(distro)
    pyfrag = f"py{py_version}"
    runtime_alias = (
        f"{py_version}{suf}" if channel == "stable" else f"{channel}-{pyfrag}{suf}"
    )
    is_stable_channel = channel == "stable"
    channel_pinned_alias = (
        f"{runtime_alias}-{sha}" if is_stable_channel else f"{ver_patch}-{pyfrag}{suf}"
    )
    secondary_tag = _stable_or_channel_tag(
        is_stable_channel=is_stable_channel,
        stable_tag=f"{ver_patch}-{pyfrag}{suf}",
        channel_pinned_alias=channel_pinned_alias,
    )
    sha_tag = _stable_or_channel_tag(
        is_stable_channel=is_stable_channel,
        stable_tag=f"{py_version}{suf}-{sha}",
        channel_pinned_alias=channel_pinned_alias,
    )

    if licensed:
        gcr_name_suffix = "langgraph-api"
        gar_name = f"{ar_project}/langgraph-api"
        # Only publish to ECR for unlicensed images
        ecr_name = None
    else:
        gcr_name_suffix = "langgraph-api-unlicensed"
        gar_name = f"{ar_project}/langgraph-api-unlicensed"
        ecr_name = f"{ecr_prefix}/langgraph-api-unlicensed"

    tags: list[str] = []
    if test_mode:
        test_tag = f"test-{pyfrag}{suf}"
        for project in projects:
            tags.append(f"{project}/{gcr_name_suffix}:{test_tag}")
        tags.append(f"{gar_name}:{test_tag}")
        if not licensed:
            if ecr_name is None:
                raise ValueError("ecr_name must be set for unlicensed tags")
            tags.append(f"{ecr_name}:{test_tag}")
        return _sorted_unique(tags)

    if licensed:
        tags.append(f"{img_api_dh}:{sha_tag}")
        if latest_on:
            tags.append(f"{img_api_dh}:{runtime_alias}")
            tags.append(f"{img_api_dh}:{secondary_tag}")

    for project in projects:
        tags.append(f"{project}/{gcr_name_suffix}:{sha_tag}")
        if latest_on:
            tags.append(f"{project}/{gcr_name_suffix}:{runtime_alias}")
            tags.append(f"{project}/{gcr_name_suffix}:{secondary_tag}")

    if licensed:
        if latest_on:
            tags.append(f"{gar_name}:{runtime_alias}")
            tags.append(f"{gar_name}:{secondary_tag}")
    else:
        if ecr_name is None:
            raise ValueError("ecr_name must be set for unlicensed tags")
        tags.append(f"{gar_name}:{runtime_alias}")
        tags.append(f"{gar_name}:{secondary_tag}")
        tags.append(f"{ecr_name}:{runtime_alias}")
        tags.append(f"{ecr_name}:{secondary_tag}")

    # Publish legacy server tags for langgraph-server
    if licensed and latest_on and is_stable_channel:
        tags.append(f"{img_svr_dh}:{ver_major}-{pyfrag}{suf}")
        tags.append(f"{img_svr_dh}:{ver_minor}-{pyfrag}{suf}")
        tags.append(f"{img_svr_dh}:{ver_patch}-{pyfrag}{suf}")
        for project in projects:
            tags.append(f"{project}/langgraph-server:{ver_major}-{pyfrag}{suf}")
            tags.append(f"{project}/langgraph-server:{ver_minor}-{pyfrag}{suf}")
            tags.append(f"{project}/langgraph-server:{ver_patch}-{pyfrag}{suf}")

    return _sorted_unique(tags)


def compute_js_publish_tags(
    *,
    node_version: str,
    tag_suffix: str,
    licensed: bool,
    sha: str,
    ver_patch: str,
    latest_on: bool,
    release_channel: str,
    test_mode: bool,
    gcr_projects: Iterable[str],
    ar_project: str,
    ecr_prefix: str,
    img_js_dh: str,
) -> list[str]:
    """Compute JS publish tags with workflow-compatible behavior."""
    node_version = _require_non_empty("node_version", node_version)
    sha = _require_non_empty("sha", sha)
    ver_patch = _require_non_empty("ver_patch", ver_patch)
    ar_project = _require_non_empty("ar_project", ar_project)
    ecr_prefix = _require_non_empty("ecr_prefix", ecr_prefix)
    img_js_dh = _require_non_empty("img_js_dh", img_js_dh)
    channel = _release_channel_alias(release_channel)

    projects = _normalize_projects(gcr_projects)
    if not projects:
        raise ValueError("gcr_projects must include at least one project")

    suffix = f"-{tag_suffix}" if tag_suffix else ""
    repo_base = "langgraphjs-api" if licensed else "langgraphjs-api-unlicensed"
    runtime_alias = (
        f"{node_version}{suffix}"
        if channel == "stable"
        else f"{channel}-node{node_version}{suffix}"
    )
    is_stable_channel = channel == "stable"
    channel_pinned_alias = (
        f"{runtime_alias}-{sha}"
        if is_stable_channel
        else f"{ver_patch}-node{node_version}{suffix}"
    )
    secondary_tag = _stable_or_channel_tag(
        is_stable_channel=is_stable_channel,
        stable_tag=f"{ver_patch}-node{node_version}{suffix}",
        channel_pinned_alias=channel_pinned_alias,
    )
    sha_tag = _stable_or_channel_tag(
        is_stable_channel=is_stable_channel,
        stable_tag=f"{node_version}{suffix}-{sha}",
        channel_pinned_alias=channel_pinned_alias,
    )

    tags: list[str] = []
    if test_mode:
        test_tag = f"test-node{node_version}{suffix}"
        for project in projects:
            tags.append(f"{project}/{repo_base}:{test_tag}")
        tags.append(f"{ar_project}/{repo_base}:{test_tag}")
        if not licensed:
            tags.append(f"{ecr_prefix}/{repo_base}:{test_tag}")
        return _sorted_unique(tags)

    if licensed:
        tags.append(f"{img_js_dh}:{sha_tag}")
        if latest_on:
            tags.append(f"{img_js_dh}:{runtime_alias}")
            tags.append(f"{img_js_dh}:{secondary_tag}")

    for project in projects:
        tags.append(f"{project}/{repo_base}:{sha_tag}")
        if latest_on:
            tags.append(f"{project}/{repo_base}:{runtime_alias}")
            tags.append(f"{project}/{repo_base}:{secondary_tag}")
        else:
            tags.append(f"{project}/{repo_base}:{secondary_tag}")

    gar_name = f"{ar_project}/{repo_base}"
    if latest_on:
        tags.append(f"{gar_name}:{runtime_alias}")
        tags.append(f"{gar_name}:{secondary_tag}")
    else:
        tags.append(f"{gar_name}:{secondary_tag}")

    if not licensed:
        ecr_name = f"{ecr_prefix}/{repo_base}"
        if latest_on:
            tags.append(f"{ecr_name}:{runtime_alias}")
            tags.append(f"{ecr_name}:{secondary_tag}")
        else:
            tags.append(f"{ecr_name}:{secondary_tag}")

    return _sorted_unique(tags)
