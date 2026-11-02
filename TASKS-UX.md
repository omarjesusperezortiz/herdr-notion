# UX round: colors, polish, ideas from herdr-bar

Source: the "UX polish" artifact. Each task is done when its tests pass (`python3 -m unittest discover -s tests -t .`) and the README and
in-app help say how to use it. Order below is the build order. Writes to Notion still need an explicit key and a confirm.

## 1. Colors

- [x] **C1 Status ramp.** One color per status from its group and position (grey, red for blocked-like names, blue, purple, teal, amber,
  orange, green), a glyph per status, 256 colors when the terminal has them and the 8 base colors otherwise. `[colors]` in the config pins a status.
  Tests: ramp for the 9-status database, blocked-like names red, override wins, 8-color fallback, glyphs.
- [x] **C2 Priority from Notion's colors.** Select options use the color set in Notion; `!!` / `!` marks for priority-like columns so the
  signal survives `NO_COLOR`. Tests: color map, fallback word list, marks.
- [x] **C3 Selection background.** The selected row keeps its colors on a soft background (herdr's `active_row_bg` when readable, else grey);
  reverse video only without colors. Tests: nearest 256 color, herdr theme read, row attrs.
- [x] **C4 Role colors.** Accent only for focus and the active tab; teal for links and code, purple for agents, grey for quiet text.

## 2. Filters and messages

- [x] **A1 Filter bar.** Pills under the tabs for tab, status, quick filter and search, each removable; `Esc` clears the last.
- [x] **A2 Toasts with undo.** Colored messages that live a few seconds, failures stay until a key; `u` undoes the last status change.
  The confirm before every write stays; `u` is its own explicit key and the toast names what it undoes.

## 3. Speed and memory

- [x] **B4 Instant start.** The last list is kept on disk and drawn at once (dimmed, "cached · updated 3h ago"), then replaced.
- [x] **A6 Remembered layout and columns.** `c` picks the columns; sort, group, compact and preview are kept per tab.

## 4. Agents and jumping

- [x] **B1 herdr glyphs and running time.** Agent column uses ◉ needs you, ◐ working, ● done, ✓ idle, with a running time (`12m`, `2h04m`).
- [x] **A3 / B3 Go-anywhere box.** `Ctrl-P` opens one fuzzy box with pages and commands; needs-you agents, then recents; the current page is
  not first; "search all of Notion" last.

## 5. Agent views

- [x] **B2 Agent preview.** `a` switches the preview between the ticket and the tail of its agent's terminal.
- [x] **B6 Agents view.** Every herdr agent and the ticket matched to it; `a` attaches an unmatched agent to a ticket.

## 6. Larger or optional

- [x] **A4 Page view in sections.** Chips for properties, subtask progress bar, tabs Body / Comments / Related.
- [x] **B5 Tab bar line.** `herdr-notion digest --bar` prints one short line for herdr's `tab_bar_right`.
- [x] **B7 Tidy up.** `X` closes the pane of a finished agent after a confirm; README has the keybinding snippet.

## 7. Small ones

- [x] **A5 Freshness.** "synced 12s ago" and an offline banner.
- [x] **A7 Empty states and tips.** Messages naming the filters in play and the keys that clear them; a rotating tip line.
- [x] **A8 Themes.** `theme = default | light | high-contrast | mono`, `Y` cycles it.
- [x] **A9 Three widths.** List only under 70 columns, list and peek from 70, list and preview from 120.

## How it was tested

Filled in at the end of the round.
