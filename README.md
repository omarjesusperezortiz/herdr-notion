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

## Create a Notion integration

1. Go to <https://www.notion.so/profile/integrations> and create a new
   **internal** integration. Give it *Read content* and *Update content*
   capabilities (update is only used when you press `s`).
2. Copy the integration secret.
3. Open your database in Notion, then `...` → **Connections** → add your
   integration. An integration only sees pages that are shared with it.
4. Copy the database id: the 32-character hex part of the database URL.
5. Store the secret outside the config file, for example in the macOS keychain:

   ```sh
   security add-generic-password -s notion-token -a "$USER" -w '<TOKEN>'
   ```

## Configure

```sh
mkdir -p "$(herdr plugin config-dir herdr-notion)"
cp config.example.toml "$(herdr plugin config-dir herdr-notion)/config.toml"
```

Open the pane from the herdr action palette (**Notion: open (split)** or
**Notion: open (tab)**), or bind a key in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+n"
type = "plugin_action"
command = "herdr-notion.open-notion"
```

### Config reference

`[notion]`

| Key | Meaning |
| --- | --- |
| `database_id` | Required. The database to browse. |
| `api_token_cmd` | Required. Shell command that prints the token. The token is never stored in the config. |
| `data_source_id` | Optional. Only for databases with several data sources; the first is used by default. |
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
- Driven end to end in a pty against mocked Notion responses: list, open,
  status change.
- **Not tested against real Notion:** authentication, the data source
  discovery and query calls, pagination, `PATCH` of a status property, and
  rarely used property and block types.
- **Not tested against a real agent:** the delegate flow, especially starting a
  new agent. `agent list`, plugin linking and manifest validation were checked
  against the installed herdr.

Not included: multiple databases, creating pages, editing properties other than
status, query pagination beyond `page_size`.

## License

MIT
