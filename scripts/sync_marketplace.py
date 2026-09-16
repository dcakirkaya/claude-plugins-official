#!/usr/bin/env python3
"""Generate a GitHub Copilot marketplace from Anthropic's marketplace."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

UPSTREAM_REPOSITORY = "anthropics/claude-plugins-official"
UPSTREAM_MARKETPLACE_PATH = ".claude-plugin/marketplace.json"
MARKETPLACE_NAME = "claude-plugins-official-copilot"
SUPPORTED_PLUGIN_FIELDS = {
    "name",
    "description",
    "version",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "category",
    "tags",
    "commands",
    "agents",
    "skills",
    "hooks",
    "mcpServers",
    "lspServers",
    "strict",
}
SOURCE_FIELDS = {
    "github": {"source", "repo", "ref", "sha", "path"},
    "url": {"source", "url", "ref", "sha", "path"},
    "git-subdir": {"source", "url", "ref", "sha", "path"},
}
SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}")


class ConversionError(ValueError):
    """An entry cannot be safely represented in a Copilot marketplace."""


@dataclass
class ConversionReport:
    converted: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"converted:  {self.converted}\n"
            f"unchanged:  {self.unchanged}\n"
            f"skipped:    {self.skipped}\n"
            f"errors:     {self.errors}"
        )


def normalize_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConversionError("source path must be a non-empty string")
    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if path.is_absolute() or not normalized or any(part in {"", ".", ".."} for part in path.parts):
        raise ConversionError(f"source path is not a safe relative path: {value!r}")
    return path.as_posix()


def parse_github_repository(url: Any) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    value = url.strip()
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        parsed = urllib.parse.urlparse(value)
        if parsed.hostname is None or parsed.hostname.lower() != "github.com":
            return None
        path = parsed.path.lstrip("/")
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    owner, repository = parts
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repository):
        return None
    return f"{owner}/{repository}"


def validate_git_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConversionError("source URL must be a non-empty string")
    url = value.strip()
    if re.fullmatch(r"[^@\s]+@[^:\s]+:.+", url):
        return url
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https", "git", "ssh"} or not parsed.netloc:
        raise ConversionError(f"unsupported or malformed Git URL: {value!r}")
    return url


def validate_optional_source_fields(source: dict[str, Any]) -> None:
    if "ref" in source and (not isinstance(source["ref"], str) or not source["ref"].strip()):
        raise ConversionError("source ref must be a non-empty string")
    if "sha" in source:
        sha = source["sha"]
        if not isinstance(sha, str) or not SHA_PATTERN.fullmatch(sha):
            raise ConversionError("source sha must be a full 40-character commit SHA")
    if "path" in source:
        normalize_relative_path(source["path"])


def ordered_source(source_type: str, source: dict[str, Any]) -> dict[str, Any]:
    order = ("source", "repo", "url", "ref", "sha", "path")
    return {key: source[key] for key in order if key in source}


def convert_source(source: Any, upstream_revision: str) -> tuple[dict[str, Any], str]:
    if isinstance(source, str):
        return (
            {
                "source": "github",
                "repo": UPSTREAM_REPOSITORY,
                "sha": upstream_revision,
                "path": normalize_relative_path(source),
            },
            "converted",
        )
    if not isinstance(source, dict):
        raise ConversionError("source must be a relative path string or an object")

    source_type = source.get("source")
    if source_type not in SOURCE_FIELDS:
        raise ConversionError(f"unsupported source type: {source_type!r}")
    unsupported = sorted(set(source) - SOURCE_FIELDS[source_type])
    if unsupported:
        raise ConversionError(
            f"unsupported properties for {source_type!r} source: {', '.join(unsupported)}"
        )
    validate_optional_source_fields(source)

    if source_type == "github":
        repo = source.get("repo")
        if not isinstance(repo, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo
        ):
            raise ConversionError(f"malformed GitHub repository name: {repo!r}")
        native = dict(source)
        if "path" in native:
            native["path"] = normalize_relative_path(native["path"])
        return ordered_source("github", native), "unchanged"

    url = validate_git_url(source.get("url"))
    if source_type == "url":
        native = dict(source)
        native["url"] = url
        if "path" in native:
            native["path"] = normalize_relative_path(native["path"])
        return ordered_source("url", native), "unchanged"

    path = normalize_relative_path(source.get("path"))
    repo = parse_github_repository(url)
    translated: dict[str, Any]
    if repo:
        translated = {"source": "github", "repo": repo}
    else:
        translated = {"source": "url", "url": url}
    for key in ("ref", "sha"):
        if key in source:
            translated[key] = source[key]
    translated["path"] = path
    return ordered_source(translated["source"], translated), "converted"


def convert_marketplace(
    upstream: dict[str, Any], upstream_revision: str
) -> tuple[dict[str, Any], dict[str, Any], ConversionReport]:
    if not SHA_PATTERN.fullmatch(upstream_revision):
        raise ValueError("upstream revision must be a full 40-character commit SHA")
    plugins = upstream.get("plugins")
    if not isinstance(plugins, list):
        raise ValueError("upstream marketplace must contain a plugins array")

    report = ConversionReport()
    converted_plugins: list[dict[str, Any]] = []
    for index, plugin in enumerate(plugins):
        label = f"plugins[{index}]"
        if isinstance(plugin, dict) and isinstance(plugin.get("name"), str):
            label = plugin["name"]
        if not isinstance(plugin, dict):
            report.skipped += 1
            report.messages.append(f"{label}: entry must be an object")
            continue
        name = plugin.get("name")
        if not isinstance(name, str) or not name:
            report.skipped += 1
            report.messages.append(f"{label}: missing non-empty plugin name")
            continue
        try:
            converted_source, status = convert_source(plugin.get("source"), upstream_revision)
        except ConversionError as error:
            report.skipped += 1
            report.messages.append(f"{name}: {error}")
            continue

        unsupported_fields = sorted(set(plugin) - SUPPORTED_PLUGIN_FIELDS - {"source"})
        if unsupported_fields:
            report.warnings.append(
                f"{name}: ignored unsupported metadata: {', '.join(unsupported_fields)}"
            )
        converted_plugin = {
            key: plugin[key] for key in SUPPORTED_PLUGIN_FIELDS if key in plugin
        }
        converted_plugin["source"] = converted_source
        converted_plugins.append(converted_plugin)
        if status == "converted":
            report.converted += 1
        else:
            report.unchanged += 1

    converted_plugins.sort(key=lambda plugin: plugin["name"])
    description = upstream.get("description")
    metadata_description = (
        f"GitHub Copilot compatibility bridge for {UPSTREAM_REPOSITORY}. "
        f"{description}" if isinstance(description, str) and description else
        f"GitHub Copilot compatibility bridge for {UPSTREAM_REPOSITORY}."
    )
    marketplace = {
        "name": MARKETPLACE_NAME,
        "owner": {"name": "Claude Plugins Copilot Bridge"},
        "metadata": {"description": metadata_description},
        "plugins": converted_plugins,
    }
    provenance = {
        "upstream_repository": UPSTREAM_REPOSITORY,
        "upstream_revision": upstream_revision,
        "upstream_manifest": UPSTREAM_MARKETPLACE_PATH,
        "upstream_marketplace_name": upstream.get("name"),
        "upstream_renames": upstream.get("renames", {}),
        "source_plugin_count": len(plugins),
        "output_plugin_count": len(converted_plugins),
        "converted": report.converted,
        "unchanged": report.unchanged,
        "skipped": report.skipped,
        "errors": report.errors,
    }
    return marketplace, provenance, report


def serialize_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def github_request(path: str) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "claude-plugins-copilot-bridge",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API request failed ({error.code}): {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"GitHub API request failed: {error.reason}") from error


def fetch_upstream(ref: str) -> tuple[dict[str, Any], str]:
    encoded_ref = urllib.parse.quote(ref, safe="")
    commit = github_request(f"/repos/{UPSTREAM_REPOSITORY}/commits/{encoded_ref}")
    revision = commit.get("sha")
    if not isinstance(revision, str) or not SHA_PATTERN.fullmatch(revision):
        raise RuntimeError("GitHub returned an invalid upstream commit SHA")
    encoded_path = urllib.parse.quote(UPSTREAM_MARKETPLACE_PATH, safe="/")
    contents = github_request(
        f"/repos/{UPSTREAM_REPOSITORY}/contents/{encoded_path}?ref={revision}"
    )
    download_url = contents.get("download_url")
    if not isinstance(download_url, str):
        raise RuntimeError("GitHub did not return a marketplace download URL")
    request = urllib.request.Request(
        download_url, headers={"User-Agent": "claude-plugins-copilot-bridge"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        marketplace = json.load(response)
    if not isinstance(marketplace, dict):
        raise RuntimeError("upstream marketplace JSON must be an object")
    return marketplace, revision


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def load_input(path: Path, revision: str | None) -> tuple[dict[str, Any], str]:
    if revision is None:
        raise ValueError("--revision is required when --input is used")
    with path.open(encoding="utf-8") as handle:
        marketplace = json.load(handle)
    if not isinstance(marketplace, dict):
        raise ValueError("input marketplace JSON must be an object")
    return marketplace, revision


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-ref", default="main")
    parser.add_argument("--input", type=Path, help="Use a local upstream marketplace fixture")
    parser.add_argument("--revision", help="Full upstream SHA for --input")
    parser.add_argument(
        "--marketplace-output",
        type=Path,
        default=Path(".github/plugin/marketplace.json"),
    )
    parser.add_argument(
        "--provenance-output",
        type=Path,
        default=Path(".github/plugin/upstream.json"),
    )
    parser.add_argument("--fail-on-skip", action="store_true")
    parser.add_argument(
        "--check", action="store_true", help="Fail if generated files are not current"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        upstream, revision = (
            load_input(args.input, args.revision)
            if args.input
            else fetch_upstream(args.upstream_ref)
        )
        marketplace, provenance, report = convert_marketplace(upstream, revision)
        marketplace_content = serialize_json(marketplace)
        provenance_content = serialize_json(provenance)

        if args.check:
            stale = []
            for path, expected in (
                (args.marketplace_output, marketplace_content),
                (args.provenance_output, provenance_content),
            ):
                if not path.exists() or path.read_text(encoding="utf-8") != expected:
                    stale.append(str(path))
            if stale:
                print(f"Generated files are stale: {', '.join(stale)}", file=sys.stderr)
                return 1
        else:
            atomic_write(args.marketplace_output, marketplace_content)
            atomic_write(args.provenance_output, provenance_content)

        print(f"upstream:   {UPSTREAM_REPOSITORY}@{revision}")
        print(report.summary())
        for message in report.messages:
            print(f"skipped: {message}", file=sys.stderr)
        for warning in report.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        if report.errors or (args.fail_on_skip and report.skipped):
            return 2
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
