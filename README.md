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

Open it from the herdr action palette (**Notion: open** covers the current pane
and returns to it on quit; **(split)** and **(tab)** variants also exist), or run
`herdr plugin action invoke herdr-notion.open-notion`. On first run it starts a short setup:

1. **Create an integration** (once): open <https://www.notion.so/profile/integrations>,
   choose **New integration**, type **Internal**, save, and copy the secret.
2. **Paste the secret** into the pane. It is hidden, checked against Notion, and
   saved next to the config in a file only you can read (`token`).
3. **Share a database with the integration** if the pane says none is shared:
   open the database in Notion, `•••` → **Connections** → add your integration.
   Then press `r` in the pane.
4. **Pick the database** and its status property from lists.

That writes `config.toml` with an "All" filter and, when your status property has
a *Complete* group, an "Open" filter. Edit the file for more filters, columns and
the delegate prompt (see below). To run the setup again, delete `config.toml`
from `herdr plugin config-dir herdr-notion`.

Token sources, first match wins: `api_token_cmd` in the config (a shell command
that prints it, for example a keychain lookup), the `NOTION_TOKEN` environment
variable, the `token` file.

Bind a key to open it, in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+n"
type = "plugin_action"
command = "herdr-notion.open-notion"
```

### Manual setup

Skip the wizard by copying the example and editing it:

```sh
mkdir -p "$(herdr plugin config-dir herdr-notion)"
cp config.example.toml "$(herdr plugin config-dir herdr-notion)/config.toml"
```

### Config reference

`[notion]`

| Key | Meaning |
| --- | --- |
| `database_id` | The database to browse (or give `data_source_id`). |
| `api_token_cmd` | Optional. Shell command that prints the token; the token is never stored in the config. Without it, `$NOTION_TOKEN` and then the `token` file are used. |
| `data_source_id` | Optional with `database_id`. Needed for databases with several data sources; the first is used by default. |
| `status_property` | Status (or select) property that `s` changes. Default `Status`. |
| `columns` | Property names shown next to the title in the list. |
| `title_property` | Property searched by `/`. Auto-detected if empty. |
| `me_user_id` | Notion user id substituted for `{me}` in filters. |
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
- Driven end to end in a pty against mocked Notion responses: setup wizard
  (bad secret, nothing shared, pick a database), list, open, status change.
- **Not tested against real Notion:** authentication, `/users/me` and `/search` used by the setup, the data source
  discovery and query calls, pagination, `PATCH` of a status property, and
  rarely used property and block types.
- **Not tested against a real agent:** the delegate flow, especially starting a
  new agent. `agent list`, plugin linking and manifest validation were checked
  against the installed herdr.

Not included: multiple databases, creating pages, editing properties other than
status, query pagination beyond `page_size`.

## License

MIT
