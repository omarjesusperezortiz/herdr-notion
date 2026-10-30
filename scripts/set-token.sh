#!/usr/bin/env bash
# Replace the saved Notion token. The secret is typed hidden and checked against Notion first.
root="$(cd "$(dirname "$0")/.." && pwd)"
PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m herdr_notion token
