#!/usr/bin/env python3
"""Validate the generated marketplace against Copilot's documented schema."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from .sync_marketplace import (
        MARKETPLACE_NAME,
        SHA_PATTERN,
        SOURCE_FIELDS,
        SUPPORTED_PLUGIN_FIELDS,
        ConversionError,
        normalize_relative_path,
        validate_git_url,
    )
except ImportError:
    from sync_marketplace import (
        MARKETPLACE_NAME,
        SHA_PATTERN,
        SOURCE_FIELDS,
        SUPPORTED_PLUGIN_FIELDS,
        ConversionError,
        normalize_relative_path,
        validate_git_url,
    )

NAME_PATTERN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
TOP_LEVEL_FIELDS = {"name", "owner", "metadata", "plugins"}
METADATA_FIELDS = {"description", "version", "pluginRoot"}
OWNER_FIELDS = {"name", "email"}
AUTHOR_FIELDS = {"name", "email", "url"}


def validate_name(value: Any, location: str) -> list[str]:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return [f"{location} must be a string between 1 and 64 characters"]
    if not NAME_PATTERN.fullmatch(value):
        return [f"{location} must use lowercase letters, numbers, dots, and hyphens"]
    return []


def validate_object_fields(
    value: Any, allowed: set[str], required: set[str], location: str
) -> list[str]:
    if not isinstance(value, dict):
        return [f"{location} must be an object"]
    errors = []
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        errors.append(f"{location} is missing: {', '.join(missing)}")
    if unknown:
        errors.append(f"{location} has unsupported fields: {', '.join(unknown)}")
    return errors


def validate_source(source: Any, location: str) -> list[str]:
    errors: list[str] = []
    if isinstance(source, str):
        try:
            normalize_relative_path(source)
        except ConversionError as error:
            errors.append(f"{location}: {error}")
        return errors
    if not isinstance(source, dict):
        return [f"{location} must be a relative path string or source object"]
    source_type = source.get("source")
    if source_type not in {"github", "url"}:
        return [f"{location}.source must be 'github' or 'url'"]
    unknown = sorted(set(source) - SOURCE_FIELDS[source_type])
    if unknown:
        errors.append(f"{location} has unsupported fields: {', '.join(unknown)}")
    if source_type == "github":
        repo = source.get("repo")
        if not isinstance(repo, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo
        ):
            errors.append(f"{location}.repo must be OWNER/REPO")
    else:
        try:
            validate_git_url(source.get("url"))
        except ConversionError as error:
            errors.append(f"{location}: {error}")
    if "ref" in source and (not isinstance(source["ref"], str) or not source["ref"]):
        errors.append(f"{location}.ref must be a non-empty string")
    if "sha" in source and (
        not isinstance(source["sha"], str) or not SHA_PATTERN.fullmatch(source["sha"])
    ):
        errors.append(f"{location}.sha must be a full 40-character commit SHA")
    if "path" in source:
        try:
            normalize_relative_path(source["path"])
        except ConversionError as error:
            errors.append(f"{location}: {error}")
    return errors


def validate_marketplace(marketplace: Any) -> list[str]:
    errors = validate_object_fields(
        marketplace, TOP_LEVEL_FIELDS, {"name", "owner", "plugins"}, "marketplace"
    )
    if not isinstance(marketplace, dict):
        return errors
    errors.extend(validate_name(marketplace.get("name"), "marketplace.name"))
    owner = marketplace.get("owner")
    errors.extend(validate_object_fields(owner, OWNER_FIELDS, {"name"}, "marketplace.owner"))
    if isinstance(owner, dict) and (
        not isinstance(owner.get("name"), str) or not owner.get("name")
    ):
        errors.append("marketplace.owner.name must be a non-empty string")
    metadata = marketplace.get("metadata")
    if metadata is not None:
        errors.extend(
            validate_object_fields(metadata, METADATA_FIELDS, set(), "marketplace.metadata")
        )
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        errors.append("marketplace.plugins must be an array")
        return errors

    names: set[str] = set()
    for index, plugin in enumerate(plugins):
        location = f"plugins[{index}]"
        errors.extend(
            validate_object_fields(
                plugin,
                SUPPORTED_PLUGIN_FIELDS | {"source"},
                {"name", "source"},
                location,
            )
        )
        if not isinstance(plugin, dict):
            continue
        name = plugin.get("name")
        errors.extend(validate_name(name, f"{location}.name"))
        if isinstance(name, str):
            if name in names:
                errors.append(f"{location}.name duplicates plugin {name!r}")
            names.add(name)
        description = plugin.get("description")
        if description is not None and (
            not isinstance(description, str) or len(description) > 1024
        ):
            errors.append(f"{location}.description must be a string of at most 1024 chars")
        author = plugin.get("author")
        if author is not None:
            errors.extend(
                validate_object_fields(author, AUTHOR_FIELDS, {"name"}, f"{location}.author")
            )
        strict = plugin.get("strict")
        if strict is not None and not isinstance(strict, bool):
            errors.append(f"{location}.strict must be a boolean")
        for key in ("keywords", "tags"):
            value = plugin.get(key)
            if value is not None and (
                not isinstance(value, list)
                or any(not isinstance(item, str) for item in value)
            ):
                errors.append(f"{location}.{key} must be an array of strings")
        errors.extend(validate_source(plugin.get("source"), f"{location}.source"))
    return errors


def validate_provenance(provenance: Any, plugin_count: int) -> list[str]:
    if not isinstance(provenance, dict):
        return ["provenance must be an object"]
    errors = []
    revision = provenance.get("upstream_revision")
    if not isinstance(revision, str) or not SHA_PATTERN.fullmatch(revision):
        errors.append("provenance.upstream_revision must be a full commit SHA")
    if provenance.get("output_plugin_count") != plugin_count:
        errors.append("provenance.output_plugin_count does not match marketplace")
    return errors


def run_copilot_validation(repository_root: Path, required: bool) -> list[str]:
    executable = shutil.which("copilot")
    if executable is None:
        if required:
            return ["copilot CLI is not installed"]
        print("Copilot CLI validation skipped: copilot is not installed")
        return []
    with tempfile.TemporaryDirectory(prefix="copilot-marketplace-validation-") as temp:
        environment = os.environ.copy()
        environment["COPILOT_HOME"] = str(Path(temp) / "home")
        environment["COPILOT_CACHE_HOME"] = str(Path(temp) / "cache")
        result = subprocess.run(
            [executable, "plugin", "marketplace", "add", str(repository_root)],
            capture_output=True,
            text=True,
            env=environment,
            timeout=60,
            check=False,
        )
    if result.returncode:
        output = (result.stderr or result.stdout).strip()
        return [f"Copilot CLI rejected the marketplace: {output}"]
    print(f"Copilot CLI accepted the marketplace ({executable})")
    return []


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "marketplace",
        nargs="?",
        type=Path,
        default=Path(".github/plugin/marketplace.json"),
    )
    parser.add_argument(
        "--provenance",
        type=Path,
        default=Path(".github/plugin/upstream.json"),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--copilot", action="store_true", help="Require Copilot CLI validation")
    group.add_argument(
        "--copilot-if-available",
        action="store_true",
        help="Use Copilot CLI validation when installed",
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        marketplace = json.loads(args.marketplace.read_text(encoding="utf-8"))
        provenance = json.loads(args.provenance.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    errors = validate_marketplace(marketplace)
    if isinstance(marketplace, dict) and isinstance(marketplace.get("plugins"), list):
        errors.extend(validate_provenance(provenance, len(marketplace["plugins"])))
    if isinstance(marketplace, dict) and marketplace.get("name") != MARKETPLACE_NAME:
        errors.append(f"marketplace.name must be {MARKETPLACE_NAME!r}")
    if args.copilot or args.copilot_if_available:
        errors.extend(run_copilot_validation(args.repo_root.resolve(), args.copilot))
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Marketplace valid: {len(marketplace['plugins'])} plugins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
