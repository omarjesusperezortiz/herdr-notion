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

That writes `config.toml` with filter tabs your database supports: **Mine**
(pages assigned to you that are not finished, opened first), **Current sprint**
when there is a checkbox or formula named like it, **Open**, and **All**. Press
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
| `f`, `1`-`9` | switch filter |
| `/` | search by title |
| `s` | change status: pick an option, then confirm with `y` |
| `d` | delegate: pick a running agent (status, cwd) or **+ start new agent…**, then the prompt is sent |
| `o` | open the page in the browser |
| `]` / `[` | next / previous page while reading one |
| `y` / `Y` | copy the page URL / the delegate prompt (pbcopy, wl-copy, xclip or xsel) |
| `r` / `R` | refresh / reload config |
| `S` | connection screen: who is signed in, the database; sign in again, pick another database, change the status property, reset the filter tabs, sign out |
| `?` | help |
| `Esc`, `q` | back / quit |

The plugin never edits Notion on its own: the only write is the status change, after an explicit confirm. The only text sent to an agent is the delegate prompt, when you pick the agent.

### Delegate

Running agents come from `herdr agent list`; the prompt is delivered with
`herdr agent prompt <pane> <text>`. **+ start new agent…** asks for an agent
type and a working directory, creates a tab (`herdr tab create`), starts the
agent with `herdr pane run`, waits until it is detected and idle, then sends
the prompt. If it never becomes ready, nothing is sent.

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
