# Claude Plugins for GitHub Copilot

This repository makes Anthropic's public Claude plugin marketplace consumable by GitHub Copilot without copying or maintaining the plugin implementations.

```text
Anthropic Marketplace
        ↓
Fetch + Translate
        ↓
Validate
        ↓
Copilot Marketplace
        ↓
GitHub Copilot App / CLI
```

## Why this bridge exists

Anthropic's marketplace uses Claude-specific source metadata such as `git-subdir`. GitHub Copilot CLI rejects those entries with:

```text
Invalid marketplace.json: plugins.<index>.source: Invalid input
```

This was reproduced on September 16, 2026 with GitHub Copilot CLI `1.0.86-0` and is also tracked in `github/copilot-cli#1996`.

Copilot currently prefers `.github/plugin/marketplace.json` and accepts:

- repository-relative plugin paths;
- `github` sources with `repo`, optional `path`, `ref`, and `sha`;
- `url` sources with optional `path`, `ref`, and `sha`.

It also discovers `.claude-plugin/marketplace.json`, but that does not make Claude-only source types valid. The GitHub Copilot app and CLI use the same repository marketplace concept; the app provides a graphical installation flow while the CLI provides `copilot plugin marketplace` commands.

## Use the marketplace

### GitHub Copilot CLI

```sh
./install.sh
```

The script finds this clone's GitHub remote, safely avoids adding the same marketplace twice, and does not edit Copilot configuration directly.

Then browse and install plugins normally:

```sh
copilot plugin marketplace browse claude-plugins-official-copilot
copilot plugin install PLUGIN_NAME@claude-plugins-official-copilot
```

You can also add the repository explicitly:

```sh
copilot plugin marketplace add dcakirkaya/claude-plugins-official
```

### GitHub Copilot app

1. Open **Customize**.
2. Select **Plugins**.
3. Select the add icon next to the marketplace dropdown.
4. Add `dcakirkaya/claude-plugins-official`.
5. Browse the marketplace and install a plugin.

Add the repository, not a raw `marketplace.json` URL.

## Generate and validate

The converter uses only the Python standard library:

```sh
python3 scripts/sync_marketplace.py --fail-on-skip
python3 -m unittest discover -s tests -v
python3 scripts/validate_marketplace.py --copilot
```

`sync_marketplace.py` resolves Anthropic's `main` branch to an exact commit, fetches the manifest at that revision, and writes:

- `.github/plugin/marketplace.json` — generated Copilot catalog;
- `.github/plugin/upstream.json` — exact upstream revision and conversion counts.

Generation is deterministic: the same upstream manifest and revision produce byte-for-byte identical files.

| Anthropic source | Copilot output |
|---|---|
| Relative path | Pinned `github` source targeting the same path in `anthropics/claude-plugins-official` |
| `url` | Preserved |
| `git-subdir` on GitHub | `github` source with `repo` and `path` |
| `git-subdir` on another Git host | `url` source with `path` |

Unsupported or malformed entries are skipped with a reason so one entry cannot poison the generated catalog. Automation passes `--fail-on-skip`, making new unsupported upstream constructs fail visibly instead of silently shrinking the marketplace.

The validator checks the documented Copilot schema, names, duplicate plugins, source types, repository names and URLs, paths, commit SHAs, unsupported properties, and provenance. `--copilot` additionally asks an installed Copilot CLI to add the repository using an isolated temporary configuration, exercising Copilot's own marketplace validator.

There is no separate marketplace validation subcommand in Copilot CLI `1.0.86-0`; an isolated local `marketplace add` is the closest product-level validation currently exposed.

## Automated updates

`.github/workflows/sync-marketplace.yml` runs daily and on demand. It regenerates, tests, and validates the marketplace, then commits changed generated files directly to `main`. Successful runs with no changes do nothing.

If generation, validation, testing, or publishing fails, the workflow stops without changing the marketplace and GitHub Actions reports a failed run. GitHub sends failed-workflow alerts according to the maintainer's Actions notification settings; successful runs create no pull request or issue.

## Limitations and final acceptance

- Claude-only top-level rename metadata and plugin `displayName` are not part of Copilot's documented marketplace schema and are not emitted.
- A plugin can be listed successfully but still depend on Claude-specific behavior. The bridge translates marketplace metadata; it does not rewrite plugin implementations or silently alter their semantics.
- CI can run Copilot's own validator only when the `copilot` executable is available; the repository's strict validator always runs.
- Installing and exercising plugins in a signed-in corporate GitHub Copilot app/account remains an interactive acceptance step.

Local CLI verification on September 16, 2026:

- the unmodified Anthropic marketplace was rejected for its `git-subdir` entries;
- the generated marketplace was accepted and all 302 entries were browsable;
- `agentforce-adlc`, `42crunch-api-security-testing`, and `agent-sdk-dev` each downloaded and reported a successful install from an isolated local marketplace;
- a signed-in interactive session and the GitHub Copilot app were not exercised, so runtime behavior is not claimed as verified.

Final app verification:

1. Add `dcakirkaya/claude-plugins-official` through **Customize → Plugins**.
2. Confirm `claude-plugins-official-copilot` appears.
3. Install one preserved `url` plugin, one translated `git-subdir` plugin, and one plugin originally represented by a relative path.
4. Start a new Copilot session and invoke a skill or agent from each plugin.

If this fails, collect the exact UI error and run:

```sh
copilot --version
copilot plugin marketplace list --json
copilot plugin marketplace browse claude-plugins-official-copilot --json
```

Copilot CLI session logs are stored under `~/.copilot/logs/`.

Current behavior references:

- https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference
- https://docs.github.com/en/copilot/how-tos/github-copilot-app/customize-github-copilot-app
- https://github.com/github/copilot-cli/issues/1996
