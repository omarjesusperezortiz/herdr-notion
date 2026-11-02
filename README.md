# herdr-notion

A Notion database TUI for a [herdr](https://herdr.dev) pane, modelled on
[herdr-jira](https://github.com/a2u/herdr-jira). Browse the pages of **one**
Notion database through named filters, search by title, read a page, change its
status, and hand a page to an agent running in herdr.

```
 Notion: Mine (3)
 Fix login bug          In progress   Ada
 Write onboarding doc   To do         Ada
 Update pricing page    In review     Ada
 Enter open · f filters · / search · s status · d delegate · o browser · ? help
```

Python 3 standard library only (urllib, curses, json). No install step, no
dependencies. Python 3.11+ uses `tomllib`; older Pythons use a small built-in
TOML parser.

> **Status:** tested against hand-written Notion JSON responses only, never
> against the real API. See [Tested](#tested).

## Install

Requires herdr 0.8.0+ and `python3`.

```sh
herdr plugin install omarjesusperezortiz/herdr-notion
```

For local development: `herdr plugin link ./herdr-notion`.

## Set up

Run `herdr plugin action invoke herdr-notion.open-notion` (or pick **Notion: open**
in the action palette). It runs Notion **in the pane you invoked it from**, as
if you had typed the command there, and quitting with `q` gives that pane's
shell back. If an agent is running in that pane it splits beside it instead, so
nothing is typed into the agent. Invoking it while Notion is already open
there does nothing. Variants: `open-notion-split`, `open-notion-overlay`,
`open-notion-tab`. On first run it starts a short setup:

1. **Sign in with Notion.** The plugin opens your browser, you choose a workspace
   and approve. Nothing is copied or pasted. If the browser is on another machine,
   press `c` to copy the link, open it there, then press `p` and paste the address
   the browser ends on.
2. **Pick the database** by typing part of its name (or pasting its Notion link),
   then its status property.

That writes `config.toml` with tabs your database supports: **Mine** (pages assigned to you that are not
finished, opened first, through the same Assignee link your Notion boards use when your person page is found), **Current sprint**
when there is a checkbox or formula named like it, **Open**, **All**, and one tab per saved Notion view (`▦ Board`, …). Press
`S` for the connection screen: it shows who is signed in and lets you sign in
again, pick another database, change the status property, reset the filter tabs
or sign out. A connection problem opens that screen by itself.

The sign-in is Notion's own OAuth for MCP clients (PKCE, no client secret). It is
stored in `oauth.json` next to the config, readable only by you, and renews
itself. Data is read and written through Notion's hosted MCP server
(`https://mcp.notion.com/mcp`). Workspace admins can restrict which MCP clients
may connect; if yours does, the sign-in page says so.

Bind a key to open it, in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+n"
type = "plugin_action"
command = "herdr-notion.open-notion"
```

### Manual setup

Skip the first-run setup by copying the example and editing it (you still sign in with the browser):

```sh
mkdir -p "$(herdr plugin config-dir herdr-notion)"
cp config.example.toml "$(herdr plugin config-dir herdr-notion)/config.toml"
```

### Config reference

`[notion]`

| Key | Meaning |
| --- | --- |
| `data_source_id` | The data source to browse (set by the setup; the `collection://` id of the database). |
| `status_property` | Status (or select) property that `s` changes. Default `Status`. |
| `columns` | Property names shown next to the title in the list. |
| `title_property` | Property searched by `/`. Auto-detected if empty. |
| `page_size` | Pages per query, default 50. |
| `mouse` | `true` turns on clicks and the wheel (default `false`; `M` toggles it while running). With the mouse on, hold Option or Shift to select text. |

`[[filters]]`: `name`, optional `filter` (a raw
[Notion filter object](https://developers.notion.com/reference/filter-data-source-entries)
written as a TOML inline table; `{me}` is substituted in strings) and optional
`sort = { property = "...", direction = "ascending" | "descending" }`. A filter
without `filter` lists everything. Search text is ANDed with the active filter.

`[delegate]`: `prompt` template, `max_body_chars`, `default_cwd`,
`startup_delay_ms`, `wait_ready_ms`, and `[[delegate.agents]]` entries
(`name`, `command` argv) offered by "start new agent". Prompt placeholders:
`{title}` `{url}` `{id}` `{status}` `{properties}` `{body}`.

The API version used is `2025-09-03`: the plugin looks up the database's data
source and queries `POST /v1/data_sources/{id}/query`.

## Keys

| Key | Action |
| --- | --- |
| `j`/`k`, arrows | move / scroll |
| `Enter` | open page (properties + body as plain text) |
| `Tab` / `Shift-Tab`, `f`, `1`-`9` | next / previous tab, pick a tab, jump to a tab |
| `/` | type to narrow the loaded pages at once; Enter also searches the whole database (a view only narrows) |
| Esc | takes the filters off one at a time, newest first (they show as pills under the chips, each with an ×; a click on the × removes it); quits when none is left |
| `[` / `]`, `0` | move along the status chips under the tabs (every status of the property, with its count; `+` means more pages exist), `0` shows every status. With more pages than are loaded, a chosen status is asked of Notion |
| `F` | pick one status to show (among the loaded pages) |
| `+` / `T` | save the current tab + status + search as a new tab / manage tabs (rename, move, delete), written to `config.toml` |
| `Ctrl-P`, `:` | Go anywhere: one fuzzy box with pages, commands and "search all of Notion". Empty, it shows agents that need you, then pages you opened lately (never the one you are on), then a few commands. Type to narrow (title, ticket id, status), ↑↓ move, `⏎` goes, `Esc` closes. Recents are kept in `recents.json` |
| `z` | compact rows: a colored bar replaces the status word (`NO_COLOR=1` turns colors off) |
| `x` | mark a page (and move down); `s` then sets one status on every marked page, `Esc` clears the marks |
| `>` | move the page to the next status (asks to confirm the first time only) |
| `M` | mouse on/off (see below) |
| `V` | open one of the database's Notion views as a tab: its filters, its order, real paging |
| `B` | Board: a column per status. `h` `l` change column, `j` `k` card, `H` `L` move the card to the next status (asks first), `⏎` opens it, `B` or `Esc` goes back to the list |
| `L` | Open a page linked from this one (a relation property); `←` comes back. The preview and the page show `☑ 2/3 subtasks · ↗ 2 related` when there are to-dos or links |
| `w` | New git worktree and branch for the ticket (named like `et-2546-fix-login-redirect`, from `main` when it exists), then an agent starts inside it with the ticket as the prompt. Asks for the repository, the branch name and a confirm. Your current checkout is not touched. `branch_prefix = "feat/"` under `[delegate]` adds a prefix |
| `Q` | Quick filter on what is loaded: changed today or this week, unread, no assignee, due this week. It shows as a pill; `Esc` removes it |
| `c` | Choose which properties the table shows and in what order (space ticks, `J` `K` move, `⏎` saves). Kept in `layout.json` next to your config |
| `a` | In a wide pane, show the selected ticket's agent (its terminal, live, re-read every 2 s) in the preview instead of the ticket; `a` again goes back |
| `A` | Agents view: every agent herdr lists with its state, how long it has been on its ticket and the ticket (delegated or attached from here, matched by ticket id, or the id written in its name). `⏎` jumps to its pane, `a` attaches it to a ticket, `r` asks herdr again, `A` or `Esc` goes back |
| `u` | Undo the last status change (single, bulk, `>`, board move) for a minute. A toast says what happened: green for done, red for a failure (stays until a key) |
| `p` | Show or hide the preview pane. Hidden, the table uses the whole width and `→` opens the page |
| `-` / `=` | Fold or unfold just the selected group / all groups (`-` turns grouping on if it is off; clicking a group header does the same) |
| `→` / `←` | `→` goes into the preview (↑↓, PgUp/PgDn, `g`/`G` scroll it) or opens the page when the pane has no preview; `←` comes back. Space and `b` scroll the preview without going in |
| `J` / `K`, Space, `b` | scroll the preview a few lines / page through the list |
| `O` / `g` | sort (newest edit, title, status) / group the rows by status (press `g` again to fold every group, once more for the plain list). While grouped, `↑` can land on a group header: `←` folds it, `→` opens it, `⏎` toggles; `←` on a page jumps up to its header, in the order the status options have in Notion (to do, in progress, done). Set `group_order = ["In progress", "To do"]` under `[notion]` to put some first |
| `s` | change status: pick an option, then confirm with `y` |
| `d` | delegate: pick a running agent (status, cwd) or **+ start new agent…**, then the prompt is sent |
| `o` | open the page in the browser |
| `]` / `[` | next / previous page while reading one |
| `y` / `Y` | copy the page URL / the delegate prompt (pbcopy, wl-copy, xclip or xsel) |
| `r` / `R` | refresh / reload config |
| `S` | connection screen: who is signed in, the database; sign in again, pick another database, change the status property, reset the filter tabs, sign out |
| `?` | help |
| `Esc`, `q` | back / quit |

In a pane 120 columns or wider the list gets a **preview pane** with the selected page's body. A **•** before a title
means the page was edited after you last looked at it, and the list refreshes itself every minute.

The plugin never edits Notion on its own: the only write is the status change, after an explicit confirm. The only text sent to an agent is the delegate prompt, when you pick the agent.

### Delegate

After you send a page, its row gets an **AGENT** cell (`◉ claude` while it works, `✔` when it is done)
and the plugin watches the agent through `herdr agent list`. When the agent goes idle after having worked,
it asks whether to set the page's status to the option named like *review* (or lets you pick one). Nothing is
written without your `y`.

Running agents come from `herdr agent list`; the prompt is delivered with
`herdr agent prompt <pane> <text>`. **+ start new agent…** asks for an agent
type and a working directory, creates a tab (`herdr tab create`), starts the
agent with `herdr pane run`, waits until it is detected and idle, then sends
the prompt. If it never becomes ready, nothing is sent.

### Mouse

Off by default: `mouse = true` in the config, or `M` while running. With it on, hold Option or Shift to select text.

Two switches have to be on. The plugin's (`M`, or `mouse = true`), and herdr's own: `[ui] mouse_capture = true` in
herdr's `config.toml` (it is the default; with `false` herdr leaves the mouse to the terminal and no pane gets clicks).
Reload herdr's config after changing it. Right click may be kept by herdr for itself: set
`right_click_passthrough_modifier = "ctrl"` under `[ui]` and use Ctrl-right-click for the menu.

| Click | What it does |
|---|---|
| a row | selects it; a second click opens it; a click in the preview gives it the focus, a click in the list takes it back |
| the status word or another cell | opens the status popup (or the editor of that property) for that row |
| the colored bar of a row | marks it; with Shift or Alt held it marks every row from the last one clicked (many terminals keep Shift for themselves) |
| `TITLE`, `STATUS`, `EDITED` | sorts by it; a second click reverses it (▼ ▲ shows the order) |
| a tab, a status chip, a group header | switches, filters, folds |
| a hint in the footer | does what its key does (`s status`, `B board`, `? more`) |
| right click on a row or card | a menu of the usual actions, each with its key (it opens on release, so a sideways trackpad swipe, which looks like a right button to the terminal library, opens nothing) |
| inside any popup | click an item to pick it, the wheel moves, a click outside cancels |
| a board card | selects it (twice opens it); drag it onto another column to change its status, after a confirm |
| the wheel | scrolls the pane under the pointer: one row per notch in the list, two lines in the preview and the page. A trackpad swipe sends dozens of events, so at most one counts every 40 ms (on macOS system Python the terminal library cannot say where the wheel turned down, so down uses the last pointer position) |

## What is remembered

Next to your config the plugin keeps a few small files (nothing goes to Notion): `seen.json` (edit times for the unread dot),
`layout.json` (per tab: sort, grouping, compact rows and whether the preview is on; and the columns you picked with `c`), and
`list-cache.json` (the first page of the last 8 tabs you looked at, so the next start draws rows at once, dimmed with
"cached · 3h ago", and swaps in the fresh ones). The cache holds page titles and the properties you show, so set `cache = false`
under `[notion]` if you would rather it did not.

## Colors

Each status has its own color and glyph, following the workflow: grey (to do), red (a name like Blocked, On hold or Waiting),
then blue, purple, teal, amber and orange along the statuses in progress in the order Notion lists them, and green for done.
Select options (Priority) use the color set in Notion; a priority-like column also gets `!!` or `!` marks, so `NO_COLOR=1` loses nothing.
The selected row keeps its colors on a soft background: your herdr theme's `active_row_bg` when `~/.config/herdr/config.toml` sets one,
else a quiet grey (reverse video on terminals without 256 colors). To pin a status to a color:

```toml
[colors]
"Review" = "orange"      # grey red blue purple teal amber orange green accent
```

## Digest

`python3 -m herdr_notion digest` (run it in the checkout) prints what changed in a tab since the last time you ran it: status
changes, new pages and edits, one line each. The first run only remembers the pages. `--tab Sprint` picks another tab
(default: the first), `--peek` reports without moving the "last seen" point. It reads the same sign-in as the app and writes
nothing to Notion; the last-seen state is a small `digest.json` next to your config.

## Tested

- Unit tests (`python3 -m unittest discover -s tests -t .`): block flattening,
  body styling, property rendering, filter/query building, status patch body, prompt
  template, config and TOML parsing, delegate command sequences with a fake
  herdr runner.
- Driven in a pty against a real Notion workspace, read only: sign-in screen, the
  connection screen, filter tabs (Mine, Current sprint, Open, All), list, open a page,
  paging between pages.
- **Not tested against real Notion:** the status change (`notion-update-page`),
  pagination beyond one page of rows (the MCP rows call returns up to 100 and no
  cursor), people names for every property type, and workspaces that block custom
  MCP clients.
- **Not tested against a real agent:** the delegate flow, especially starting a
  new agent. `agent list`, plugin linking and manifest validation were checked
  against the installed herdr.

Not included: multiple databases, creating pages, editing properties other than
status, query pagination beyond `page_size`.

## License

MIT
