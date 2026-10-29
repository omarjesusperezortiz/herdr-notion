#!/usr/bin/env bash
# Launch-or-focus the Notion pane. Usage: open-notion.sh [overlay|split|tab]
# overlay (default) covers the pane you invoked it from and closes on quit.
#
# herdr has no "find my pane" call, so the pane id is remembered in the plugin
# state dir. If focusing it fails (the pane was closed) a new one is opened.
set -uo pipefail

herdr_bin="${HERDR_BIN_PATH:-herdr}"
placement="${1:-overlay}"
state_dir="${HERDR_PLUGIN_STATE_DIR:-${TMPDIR:-/tmp}/herdr-notion}"
state="$state_dir/pane_id"

if [ -s "$state" ] && "$herdr_bin" plugin pane focus "$(cat "$state")" >/dev/null 2>&1; then
  exit 0
fi

args=(plugin pane open --plugin herdr-notion --entrypoint notion --placement "$placement" --focus)
if [ "$placement" = "split" ]; then
  args+=(--direction right)
fi

out="$("$herdr_bin" "${args[@]}")" || { printf '%s\n' "$out"; exit 1; }

mkdir -p "$state_dir"
printf '%s' "$out" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["plugin_pane"]["pane"]["pane_id"])' \
  > "$state" 2>/dev/null || rm -f "$state"
printf '%s\n' "$out"
