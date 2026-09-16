#!/bin/sh
set -eu

marketplace_name="claude-plugins-official-copilot"

if ! command -v copilot >/dev/null 2>&1; then
  echo "GitHub Copilot CLI is not installed or is not on PATH." >&2
  echo "Install it, then rerun ./install.sh." >&2
  exit 1
fi

source="${1:-}"
if [ -z "$source" ]; then
  remote="$(git -C "$(dirname "$0")" remote get-url origin 2>/dev/null || true)"
  case "$remote" in
    https://github.com/*.git)
      source="${remote#https://github.com/}"
      source="${source%.git}"
      ;;
    git@github.com:*.git)
      source="${remote#git@github.com:}"
      source="${source%.git}"
      ;;
    *)
      source="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
      ;;
  esac
fi

if copilot plugin marketplace list --json 2>/dev/null |
  python3 -c 'import json,sys; name=sys.argv[1]; data=json.load(sys.stdin); raise SystemExit(0 if any(item.get("name")==name for item in data) else 1)' "$marketplace_name"
then
  echo "Marketplace '$marketplace_name' is already registered."
else
  echo "Adding marketplace from $source"
  copilot plugin marketplace add "$source"
fi

cat <<EOF

Marketplace ready.

Browse:
  copilot plugin marketplace browse $marketplace_name

Install:
  copilot plugin install PLUGIN_NAME@$marketplace_name
EOF
