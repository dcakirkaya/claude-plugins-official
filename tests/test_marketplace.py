from __future__ import annotations

import copy
import unittest

from scripts.sync_marketplace import (
    UPSTREAM_REPOSITORY,
    convert_marketplace,
    convert_source,
    parse_github_repository,
    serialize_json,
)
from scripts.validate_marketplace import validate_marketplace

REVISION = "1" * 40


def upstream(*plugins):
    return {
        "name": "claude-plugins-official",
        "description": "Test marketplace",
        "owner": {"name": "Anthropic"},
        "plugins": list(plugins),
    }


class SourceConversionTests(unittest.TestCase):
    def test_native_compatible_source_is_preserved(self):
        source = {
            "source": "url",
            "url": "https://github.com/example/plugin.git",
            "sha": "2" * 40,
        }
        converted, status = convert_source(source, REVISION)
        self.assertEqual(converted, source)
        self.assertEqual(status, "unchanged")

    def test_git_subdir_converts_to_github_source(self):
        converted, status = convert_source(
            {
                "source": "git-subdir",
                "url": "https://github.com/OWNER/REPO.git",
                "path": "some/plugin/path",
                "ref": "main",
                "sha": "2" * 40,
            },
            REVISION,
        )
        self.assertEqual(
            converted,
            {
                "source": "github",
                "repo": "OWNER/REPO",
                "ref": "main",
                "sha": "2" * 40,
                "path": "some/plugin/path",
            },
        )
        self.assertEqual(status, "converted")

    def test_git_suffix_is_removed_from_github_repository(self):
        self.assertEqual(
            parse_github_repository("https://github.com/OWNER/REPO.git"),
            "OWNER/REPO",
        )

    def test_github_url_parsing_supports_ssh(self):
        self.assertEqual(
            parse_github_repository("git@github.com:OWNER/REPO.git"),
            "OWNER/REPO",
        )
        self.assertIsNone(parse_github_repository("https://gitlab.com/OWNER/REPO.git"))

    def test_ref_is_preserved_for_non_github_git_subdir(self):
        converted, _ = convert_source(
            {
                "source": "git-subdir",
                "url": "https://gitlab.com/owner/repo.git",
                "path": "./plugins/test",
                "ref": "release",
            },
            REVISION,
        )
        self.assertEqual(converted["source"], "url")
        self.assertEqual(converted["ref"], "release")
        self.assertEqual(converted["path"], "plugins/test")

    def test_relative_source_points_to_pinned_upstream_subdirectory(self):
        converted, _ = convert_source("./plugins/test", REVISION)
        self.assertEqual(
            converted,
            {
                "source": "github",
                "repo": UPSTREAM_REPOSITORY,
                "sha": REVISION,
                "path": "plugins/test",
            },
        )


class MarketplaceConversionTests(unittest.TestCase):
    def test_malformed_entry_is_skipped(self):
        marketplace, _, report = convert_marketplace(
            upstream(
                {"name": "valid", "source": "plugins/valid"},
                {"name": "broken", "source": {"source": "url", "url": "not-a-url"}},
            ),
            REVISION,
        )
        self.assertEqual([plugin["name"] for plugin in marketplace["plugins"]], ["valid"])
        self.assertEqual(report.skipped, 1)
        self.assertIn("broken:", report.messages[0])

    def test_unknown_source_type_is_skipped(self):
        marketplace, _, report = convert_marketplace(
            upstream({"name": "broken", "source": {"source": "archive", "url": "x"}}),
            REVISION,
        )
        self.assertEqual(marketplace["plugins"], [])
        self.assertEqual(report.skipped, 1)
        self.assertIn("unsupported source type", report.messages[0])

    def test_generation_is_deterministic(self):
        fixture = upstream(
            {"name": "z-last", "source": "plugins/z"},
            {
                "name": "a-first",
                "source": {
                    "source": "url",
                    "url": "https://github.com/example/a.git",
                    "sha": "2" * 40,
                },
            },
        )
        first = convert_marketplace(copy.deepcopy(fixture), REVISION)
        second = convert_marketplace(copy.deepcopy(fixture), REVISION)
        self.assertEqual(serialize_json(first[0]), serialize_json(second[0]))
        self.assertEqual(serialize_json(first[1]), serialize_json(second[1]))
        self.assertEqual(
            [plugin["name"] for plugin in first[0]["plugins"]],
            ["a-first", "z-last"],
        )

    def test_duplicate_names_are_detected(self):
        marketplace, _, _ = convert_marketplace(
            upstream(
                {"name": "duplicate", "source": "plugins/one"},
                {"name": "duplicate", "source": "plugins/two"},
            ),
            REVISION,
        )
        errors = validate_marketplace(marketplace)
        self.assertTrue(any("duplicates plugin" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
