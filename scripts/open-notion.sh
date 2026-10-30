#!/usr/bin/env bash
# Launch-or-focus the Notion pane. Usage: open-notion.sh [here|split|overlay|tab]
#
#   here     (default) run Notion inside the pane you invoked this from; quitting
#            returns that pane's shell. Only used when the pane is a plain shell
#            (no agent detected); otherwise falls back to split.
#   split    open beside the invoking pane, in the same tab (one per tab).
#   overlay  zoomed overlay over the invoking pane.
#   tab      its own tab.
#
# herdr has no "find my pane" call, so split/overlay/tab remember the pane id in
# the plugin state dir and focus it next time; a new one opens if it is gone.
set -uo pipefail

herdr_bin="${HERDR_BIN_PATH:-herdr}"
placement="${1:-here}"
root="${HERDR_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
state_dir="${HERDR_PLUGIN_STATE_DIR:-${TMPDIR:-/tmp}/herdr-notion}"

pane_field() { # pane_field <pane> <agent|terminal_title_stripped>
  "$herdr_bin" pane get "$1" 2>/dev/null | python3 -c '
import json, sys
p = json.load(sys.stdin)["result"]["pane"]
print(p.get(sys.argv[1]) or "")' "$2" 2>/dev/null
}

if [ "$placement" = "here" ]; then
  pane="${HERDR_PANE_ID:-}"
  if [ -n "$pane" ] && "$herdr_bin" pane get "$pane" >/dev/null 2>&1; then
    # already showing Notion: typing the command now would go into the TUI
    case "$(pane_field "$pane" terminal_title_stripped)" in *herdr-notion*) exit 0 ;; esac
    if [ -z "$(pane_field "$pane" agent)" ]; then
      cmd="$(printf 'PYTHONPATH=%q' "$root")"
      [ -n "${HERDR_PLUGIN_CONFIG_DIR:-}" ] && cmd+="$(printf ' HERDR_PLUGIN_CONFIG_DIR=%q' "$HERDR_PLUGIN_CONFIG_DIR")"
      [ -n "${HERDR_PLUGIN_STATE_DIR:-}" ] && cmd+="$(printf ' HERDR_PLUGIN_STATE_DIR=%q' "$HERDR_PLUGIN_STATE_DIR")"
      exec "$herdr_bin" pane run "$pane" "$cmd python3 -m herdr_notion"
    fi
  fi
  placement="split"
fi

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
