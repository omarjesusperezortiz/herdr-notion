# UI tasks

Source: the UI proposal. Each task is done only when its tests pass (`python3 -m unittest discover -s tests`).

- [ ] **T0 Preview without a mode.** Space/b, PgUp/PgDn, J/K already scroll the preview from the list. Add the visible cue
  `Space ▾ more · NN%` whenever the preview has more text, and list the scroll keys in the footer. Tab stays optional.
  Tests: the cue shows only when text overflows; Space/b scroll without Tab; the list selection does not move.
- [ ] **T1 Status chips with counts.** Second header row: `All 42 · To do 12 · In progress 9 · Done 21`. `[` `]` move along
  the chips, `0` clears. Counts come from loaded pages. Hidden when the pane is too narrow.
  Tests: counts, chip order follows the status options, `[` `]` `0` change the filter, narrow pane hides the row.
- [ ] **T2 Status bar color and density.** A colored `▌` per row by status (grey, yellow, green), red for high priority,
  `NO_COLOR` respected. `z` switches the table (status word on every row) and compact (bar only, word on the selected row).
  Not built: two-line rows (the table is already the roomy view).
  Tests: compact drops the STATUS column and gives the title more room; the selected row still names its status.
- [ ] **T3 Save a view as a tab, tab manager, command palette.** `+` saves the current tab, status and search as a new tab.
  `T` opens the tab manager (rename, move, delete). `:` opens a palette of every command. Written to `config.toml` after a confirm.
  Tests: the saved filter combines tab, status and text; the written file loads back; delete/move/rename; palette runs a command.
- [ ] **T4 Mouse (opt-in).** `mouse = true` in config and `M` to toggle. Click selects, double click opens, click a tab or chip
  switches, wheel scrolls the preview or the list. Default off.
  Tests with fake mouse events only. Whether herdr forwards mouse events to a plugin pane is NOT verified.
- [ ] **T5 Marks and advance status.** `x` marks rows, `s` applies one status to all marked after one confirm.
  `>` moves the page to the next status.
  Tests: bulk write goes through once per page; `>` picks the next option; declining writes nothing.
- [ ] **T6 Board view.** Not built. Largest piece and marked "only if you want it".

Last: README key table, in-app help, push, report what was tested only against fakes.
