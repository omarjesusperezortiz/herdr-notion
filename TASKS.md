# UI tasks

Source: the UI proposal. Each task is done only when its tests pass (`python3 -m unittest discover -s tests`).

- [x] **T0 Preview without a mode.** Space/b, PgUp/PgDn, J/K already scroll the preview from the list. Add the visible cue
  `Space ▾ more · NN%` whenever the preview has more text, and list the scroll keys in the footer. Tab stays optional.
  Tests: the cue shows only when text overflows; Space/b scroll without Tab; the list selection does not move.
- [x] **T1 Status chips with counts.** Second header row: `All 42 · To do 12 · In progress 9 · Done 21`. `[` `]` move along
  the chips, `0` clears. Counts come from loaded pages. Hidden when the pane is too narrow.
  Tests: counts, chip order follows the status options, `[` `]` `0` change the filter, narrow pane hides the row.
- [x] **T2 Status bar color and density.** A colored `▌` per row by status (grey, yellow, green), red for high priority,
  `NO_COLOR` respected. `z` switches the table (status word on every row) and compact (bar only, word on the selected row).
  Not built: two-line rows (the table is already the roomy view); zoom `z` in the page view.
  Tests: compact drops the STATUS column and gives the title more room; the selected row still names its status.
- [x] **T3 Save a view as a tab, tab manager, command palette.** `+` saves the current tab, status and search as a new tab.
  `T` opens the tab manager (rename, move, delete). `:` opens a palette of every command. Written to `config.toml` after a confirm.
  Tests: the saved filter combines tab, status and text; the written file loads back; delete/move/rename; palette runs a command.
- [x] **T4 Mouse (opt-in).** `mouse = true` in config and `M` to toggle. Click selects, double click opens, click a tab or chip
  switches, wheel scrolls the preview or the list. Default off.
  Tests with fake mouse events only. Whether herdr forwards mouse events to a plugin pane is NOT verified.
- [x] **T5 Marks and advance status.** `x` marks rows, `s` applies one status to all marked after one confirm.
  `>` moves the page to the next status.
  Tests: bulk write goes through once per page; `>` picks the next option; declining writes nothing.
- [ ] **T6 Board view.** Not built. Largest piece and marked "only if you want it".

Last: README key table, in-app help, push, report what was tested only against fakes.

Not built from T4: clicking a footer hint. Wheel-down relies on ncurses reporting button 5; checked only with fake events.

# Round 2 (from the "next round" artifact)

Each is done only when its tests pass. Whatever is tested only against fakes is listed at the end.

- [x] **R1 Quick filters in the palette.** Changed today, changed this week, assigned to me, no assignee, due this week.
  They stack on the current tab, show in the header, and Esc clears them.
- [x] **R2 Agent badges.** Delegation puts the page id in the prompt. A herdr agent whose prompt or recent output holds
  that id is matched to the row (working, idle). A finished agent offers to set the status.
- [x] **R3 Board view.** `B` opens columns by status. h/l column, j/k card, H/L move the card (confirm), Enter opens.
- [x] **R4 Subtasks and relations in the preview.** A summary line (to-do progress, linked pages) in the preview and the page; `L` opens a linked page, `←` comes back. Sub-items are counted from the page's to-do lines.
- [x] **R5 Digest.** `python3 -m herdr_notion digest [--tab NAME] [--peek]` prints status changes, new pages and edits since the last run. It keeps its own `digest.json` (the seen file only has edit times, so it cannot tell a status change).
- [x] **R6 Branch from a ticket.** `w` (not `b`, which scrolls the preview) makes a git worktree and branch, asks first, and starts an agent in it. A worktree keeps your current checkout untouched; the mockup said "create a branch".

Tested only against fakes: agent matching (herdr is faked), the board and quick-filter screens (fake terminal), moving a card
and starting a branch's agent (fake client and fake herdr). Tested for real: the git worktree creation (a temporary
repository), the digest command's read path against the real database (read-only, `--peek`).

# Round 3: the mouse proposals

- [x] **M1 Popups take the mouse** (click picks, wheel moves, click outside cancels; the help box scrolls with the wheel).
- [x] **M2 Footer hints are buttons** (list, page, board; a hint naming several keys is not).
- [x] **M3 Click a cell** (status word: status popup; other cells: that property's editor).
- [x] **M4 Board drag** (press a card, release over another column: confirm, then the status is set).
- [x] **M5 Right-click menu** (row or card; the box opens at the pointer).
- [x] **M6 Column headers sort** (second click reverses; ▼ ▲). The EDITED column is two characters wider for the arrow.
- [x] **M7 Click the bar to mark**, Shift or Alt click for a range. Many terminals keep Shift for text selection, so Alt may be the one that works.
- [x] **M8 A click focuses the pane** (done earlier).

Checked for real: the app reads real X10 mouse bytes sent to a pty (click, bar, header, right-click menu, footer hint, status popup,
board drag with the confirm declined). Not verified: that herdr forwards mouse events to a plugin pane; whether Shift/Alt arrive in your terminal.
