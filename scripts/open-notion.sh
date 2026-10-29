#!/usr/bin/env bash
# Launch-or-focus the Notion pane. Usage: open-notion.sh [split|overlay|tab]
# split (default) opens beside the pane you invoked it from, in the same tab;
# one Notion pane per tab. overlay covers that pane; tab opens a new tab.
#
# herdr has no "find my pane" call, so the pane id is remembered in the plugin
# state dir. If focusing it fails (the pane was closed) a new one is opened.
set -uo pipefail

herdr_bin="${HERDR_BIN_PATH:-herdr}"
placement="${1:-split}"
state_dir="${HERDR_PLUGIN_STATE_DIR:-${TMPDIR:-/tmp}/herdr-notion}"
state="$state_dir/pane_id.${HERDR_TAB_ID:-default}"  # one pane per tab

if [ -s "$state" ] && "$herdr_bin" plugin pane focus "$(cat "$state")" >/dev/null 2>&1; then
  exit 0
fi

args=(plugin pane open --plugin herdr-notion --entrypoint notion --placement "$placement" --focus)
if [ "$placement" = "split" ]; then
  args+=(--direction right)
  # split the pane the action was invoked from, so it lands in that tab
  if [ -n "${HERDR_PANE_ID:-}" ]; then
    args+=(--target-pane "$HERDR_PANE_ID")
  fi
fi

out="$("$herdr_bin" "${args[@]}")" || { printf '%s\n' "$out"; exit 1; }

mkdir -p "$state_dir"
printf '%s' "$out" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["plugin_pane"]["pane"]["pane_id"])' \
  > "$state" 2>/dev/null || rm -f "$state"
printf '%s\n' "$out"
