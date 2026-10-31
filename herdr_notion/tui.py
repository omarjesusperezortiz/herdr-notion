"""curses TUI: list, detail, popups. All network calls are synchronous."""
import curses
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import traceback
import webbrowser

from . import config as cfgmod
from . import delegate as dg
from . import mcpbackend as mb
from . import oauth
from . import notion as nt
from . import setup as st
from . import tomlcompat

HELP = [
    ("Navigate", [("j/k, arrows", "move / scroll"), ("Enter", "open page"),
                  ("] / [", "next / previous page (in a page)"),
                  ("f, 1-9", "switch filter"), ("/", "type to narrow, Enter searches everything"), ("Esc", "clear search and filters, then quit"), ("q", "quit")]),
    ("Act", [("s", "change status (asks to confirm; on marked pages too)"), ("x", "mark a page; s then changes every marked page"), (">", "move to the next status"), ("e", "edit another property (asks to confirm)"), ("n", "new page in this database"), ("c", "comment on the page you are reading"), ("D", "switch database"), ("d", "delegate to an agent"), ("O", "sort: newest edit, title, status"), ("g", "group by status"), ("M", "mouse on/off (config: mouse = true)"), ("+", "save the current view as a tab"), ("T", "manage tabs: rename, move, delete"), (":", "command palette"), ("z", "compact rows: colored bar instead of the status word"), ("F", "pick a status to show"), ("[ ] , 0", "previous / next status chip, 0 shows all"), ("Tab", "give the arrows to the preview (optional: Space/b already scroll it)"), ("← →, h l", "previous / next tab"), ("V", "open a Notion view of this database"), ("J / K, Space, b", "scroll the preview / page the list"),
             ("o", "open in browser"), ("y", "copy page URL"), ("Y", "copy delegate prompt")]),
    ("Config", [("r", "refresh"), ("R", "reload config"), ("S", "connection: status, sign in, database, status property")]),
]

class App:
    def __init__(self, scr):
        self.scr = scr
        self.msg = ""
        self.cfg = None
        self.client = None
        self.reconnect = ""  # set by guard() on a connection problem; the main loop opens the Connect screen
        self.job = None  # background load in flight: {"seq", "label", "kind", "done", "result", "error", "on_done"}
        self.job_seq = 0
        self.onboarding = False
        self.picked_title, self.picked_tabs = "", []
        self.want_quit = False
        self.sort_mode = 0  # index into SORTS
        self.group = False  # group rows under their status
        self.seen = self.load_seen()  # page id -> last edit time already looked at (for the unread dot)
        self.last_refresh = time.time()
        self.sel_id, self.sel_changed = None, time.time()
        self.delegations = self.load_delegations()  # page id -> {"pane", "agent", "title", "seen_working", "done"}
        self.finished = []  # page ids whose agent just went idle: the main loop offers to move them to review
        self.next_agent_poll = 0
        self.fidx = 0
        self.query = ""
        self.all = []  # every page loaded for this tab, in the chosen order
        self.pages = []  # what is shown: `all` narrowed by the type-ahead text and the status filter
        self.live = ""  # type-ahead text (filters the loaded pages as you type)
        self.status_filter = ""  # show only this status
        self.pscroll = 0  # scroll offset of the preview pane
        self.mouse_on = False
        self.last_click, self.last_click_at = None, 0.0
        self.server_status = False  # the status filter is part of the query (the tab has more pages than are loaded)
        self.more_all = False  # with server_status: whether the unfiltered tab had more pages
        self.chip_counts = {}  # with server_status: the counts the chips showed before the query narrowed
        self.marked = set()  # page ids chosen with x for a bulk status change
        self.advance_ok = False  # the > key asks to confirm only the first time
        self.compact = False  # z: bar only, no status column (the table shows the status word on every row)
        self.hits = []  # clickable regions of the last drawing: (y, x0, x1, action, argument)
        self.focus = "list"  # "preview" while the arrow keys scroll the preview pane
        self.status_order = {}  # status name -> position (the order of the options in Notion, or group_order)
        self.has_more = False
        self.cursor = None
        self.query_body = {}
        self.sel = 0
        self.top = 0
        self.detail = None  # {"page", "lines"}
        self.dscroll = 0
        self.cache = {}  # page id -> body lines
        self.title_prop = ""
        self.groups = {}
        self.colors = False

    # ------------------------------------------------------------ setup
    def load_config(self):
        cfg = cfgmod.load()
        if not cfg["data_source_id"]:
            raise cfgmod.NeedsSetup("no database picked yet")
        self.client = mb.McpClient(cfg["data_source_id"])
        self.client.want = set(cfg["columns"])
        self.title_prop = cfg["title_property"]
        self.groups = {}
        self.fidx = 0
        self.cfg = cfg
        self.set_mouse(cfg["mouse"])

    # ------------------------------------------------------------ mouse (opt-in)
    WHEEL_UP = getattr(curses, "BUTTON4_PRESSED", 0x80000)
    WHEEL_DOWN = getattr(curses, "BUTTON5_PRESSED", 0x200000)  # ncurses 6 reports the wheel down as button 5

    def set_mouse(self, on):
        """Ask the terminal for clicks and the wheel (or stop asking). While on, dragging selects nothing and
        Cmd-click opens no links; Option (macOS Terminal, iTerm) or Shift (most others) gives that back."""
        self.mouse_on = bool(on)
        try:
            curses.mousemask(curses.BUTTON1_PRESSED | self.WHEEL_UP | self.WHEEL_DOWN if on else 0)
            curses.mouseinterval(0)
        except curses.error:
            self.mouse_on = False

    def toggle_mouse(self):
        self.set_mouse(not self.mouse_on)
        self.msg = ("mouse on: click a row, tab or status, wheel scrolls · hold Option or Shift to select text"
                    if self.mouse_on else "mouse off")

    def on_mouse(self):
        """A mouse event arrived: the wheel scrolls the pane under the pointer, a click selects, a second click opens."""
        try:
            _, x, y, _, bstate = curses.getmouse()
        except curses.error:
            return True
        w = self.scr.getmaxyx()[1]
        if self.detail:
            if bstate & self.WHEEL_UP:
                self.dscroll = max(0, self.dscroll - 3)
            elif bstate & self.WHEEL_DOWN:
                self.dscroll += 3
            return True
        wheel = -3 if bstate & self.WHEEL_UP else 3 if bstate & self.WHEEL_DOWN else 0
        if wheel:
            tw = max(70, int(w * 0.56))
            if self.wide() and x > tw:
                self.pscroll = max(0, self.pscroll + wheel)
            elif self.pages:
                self.sel = max(0, min(len(self.pages) - 1, self.sel + wheel))
            return True
        if not bstate & curses.BUTTON1_PRESSED:
            return True
        hit = next((h for h in self.hits if h[0] == y and h[1] <= x < h[2]), None)
        if not hit:
            return True
        action, arg = hit[3], hit[4]
        if action == "tab":
            self.guard(self.pick_filter, arg)
        elif action == "status":
            self.guard(self.set_status_filter, arg)
        elif action == "row" and arg < len(self.pages):
            again = self.last_click == (y, self.pages[arg]["id"]) and time.time() - self.last_click_at < 0.5
            self.sel, self.focus = arg, "list"
            self.last_click, self.last_click_at = (y, self.pages[arg]["id"]), time.time()
            if again:
                self.guard(self.open_page, self.pages[arg])
        return True

    def busy(self, text):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        self.scr.addnstr(h // 2, 2, text, w - 4)
        self.scr.refresh()

    def note(self, text):
        """Show `text` in the footer over the screen as it is, without blanking it, then keep working."""
        self.msg = text
        (self.draw_detail if self.detail else self.draw_list)()
        self.scr.refresh()

    def guard(self, fn, *a):
        """Run fn, turning expected errors into a status message."""
        try:
            return fn(*a)
        except (nt.NotionError, dg.HerdrError, cfgmod.ConfigError, oauth.OAuthError) as e:
            self.report(e)
        return None

    def report(self, e):
        self.msg = str(e)
        if "Could not find" in self.msg:  # the account cannot see the configured database
            self.msg = "This token cannot see the configured database."
        if self.connection_problem(self.msg):
            self.reconnect = self.msg

    # ------------------------------------------------------------ background loading
    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def visible_job(self):
        return bool(self.job and self.job["kind"] not in ("agents", "preview", "silent"))

    def spin(self):
        return self.SPINNER[int(time.time() * 10) % len(self.SPINNER)]

    def start_job(self, label, work, on_done, kind="list"):
        """Run `work` in a thread; `on_done(result)` runs on the main loop when it finishes. A newer job replaces
        an older one (the older result is dropped), so switching tabs quickly never shows stale rows."""
        self.job_seq += 1
        box = {"seq": self.job_seq, "label": label, "kind": kind, "done": False, "on_done": on_done}

        def run():
            try:
                box["result"] = work()
            except Exception as e:  # handed to the main loop
                box["error"] = e
            box["done"] = True

        self.job = box
        threading.Thread(target=run, daemon=True).start()

    def poll_job(self):
        job = self.job
        if not job or not job["done"]:
            return
        self.job = None
        if "error" in job:
            e = job["error"]
            if isinstance(e, (nt.NotionError, dg.HerdrError, cfgmod.ConfigError, oauth.OAuthError)):
                self.report(e)
                return
            raise e
        job["on_done"](job["result"])

    @staticmethod
    def connection_problem(msg):
        return any(x in msg for x in ("cannot see the configured database", "API token is invalid", "Notion 401",
                                      "not signed in", "login expired", "Notion MCP 401"))

    # ------------------------------------------------------------ data
    def build_list_query(self, schema):
        if not self.title_prop:
            self.title_prop = nt.title_property_name(schema.get("properties", {}))
        flt = self.cfg["filters"][self.fidx]
        if flt.get("view"):  # a saved Notion view: its own filters and order, with real paging
            return {"view_url": flt["view"], "page_size": self.cfg["page_size"]}
        body = nt.build_query(flt, self.query, self.title_prop, "me", self.cfg["page_size"])
        if self.server_status and self.status_filter:
            prop = self.cfg["status_property"]
            cond = {"property": prop, nt.status_options(schema, prop)[0]: {"equals": self.status_filter}}
            body["filter"] = {"and": [body["filter"], cond]} if body.get("filter") else cond
        return body

    def set_status_order(self, schema):
        """Order of the statuses for sorting and grouping: the order of the options in Notion (not started, started,
        finished, as on a board), unless `group_order` in the config says otherwise."""
        try:
            opts = nt.status_options(schema, self.cfg["status_property"])[1]
        except nt.NotionError:
            opts = []
        order = list(self.cfg.get("group_order") or []) + [o for o in opts if o not in (self.cfg.get("group_order") or [])]
        self.status_order = {n: i for i, n in enumerate(order)}

    def status_rank(self, page):
        text = nt.prop_text(page["properties"].get(self.cfg["status_property"]))
        return (self.status_order.get(text, 999), text.lower())

    def apply_list(self, schema, body, res):
        self.groups = nt.status_groups(schema, self.cfg["status_property"])
        self.set_status_order(schema)
        self.query_body = body
        self.all = res.get("results", [])
        self.live = ""
        self.has_more, self.cursor = bool(res.get("has_more")), res.get("cursor")
        self.sel = self.top = 0
        self.msg = ""
        self.apply_sort()
        self.sel = self.top = 0  # a new list starts at its first row, whatever the sort did
        self.baseline_seen()
        self.last_refresh = time.time()

    def refresh(self):
        """Blocking load, for flows that need the list right away."""
        self.busy("Loading…")
        schema = self.client.discover()
        body = self.build_list_query(schema)
        self.apply_list(schema, body, self.client.query(body))

    def refresh_async(self, label="Loading…"):
        """Load the list in the background: the current rows stay on screen (dimmed) until the new ones arrive."""
        def work():
            schema = self.client.discover()
            body = self.build_list_query(schema)
            return schema, body, self.client.query(body)

        self.start_job(label, work, lambda r: self.apply_list(*r), "list")

    def load_more(self):
        """Append the next page of results (called when the selection reaches the end of the list)."""
        if not self.has_more or not self.cursor or self.job:
            return
        body, cursor = self.query_body, self.cursor
        seen = {p["id"] for p in self.pages}

        def done(res):
            have = {p["id"] for p in self.all}
            self.all += [p for p in res.get("results", []) if p["id"] not in have]
            self.has_more, self.cursor = bool(res.get("has_more")), res.get("cursor")
            self.msg = res.get("note", "")
            self.apply_sort()
            self.baseline_seen()

        self.start_job("loading more…", lambda: self.client.query(body, cursor=cursor, seen=seen), done, "more")

    def open_page(self, page):
        """Show the page at once with what the list already knows; the body fills in when it arrives."""
        pid = page["id"]
        self.mark_seen(page)
        self.detail = {"page": page, "lines": [], "body": "Loading…" if pid not in self.cache else "\n".join(self.cache[pid])}
        self.dscroll = 0

        def work():
            fresh = self.client.get_page(pid)
            try:
                comments = self.client.comments(pid)
            except (nt.NotionError, AttributeError):
                comments = []
            return fresh, self.cache.get(pid) or self.client.body_lines(pid), comments

        def done(r):
            fresh, body, comments = r
            self.cache[pid] = body
            if self.detail and self.detail["page"]["id"] == pid:
                self.detail = {"page": fresh, "lines": [], "body": "\n".join(body), "comments": comments}

        self.start_job("Loading page…", work, done, "detail")

    def current(self):
        if self.detail:
            return self.detail["page"]
        return self.pages[self.sel] if self.pages else None

    # ------------------------------------------------------------ sorting, grouping, unread
    SORTS = [("edited", "newest edit"), ("title", "title A-Z"), ("status", "status")]
    KIND_RANK = {"progress": 0, "todo": 1, "done": 2, "": 3}

    def status_kind(self, page):
        text = nt.prop_text(page["properties"].get(self.cfg["status_property"]))
        return nt.group_kind(self.groups.get(text, "")) or nt.guess_kind(text)

    @staticmethod
    def fold(text):
        """Lower-case without accents, so 'revisión' matches 'revision'."""
        import unicodedata
        return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")

    def matches(self, page):
        if self.live and self.fold(self.live) not in self.fold(nt.page_title(page)):
            return False
        if self.status_filter and nt.prop_text(page["properties"].get(self.cfg["status_property"])) != self.status_filter:
            return False
        return True

    def refilter(self):
        """Recompute the shown pages from `all`, keeping the selection on the same page when it is still shown."""
        sid = self.pages[self.sel]["id"] if self.pages and self.sel < len(self.pages) else None
        self.pages = [p for p in self.all if self.matches(p)]
        self.sel = next((i for i, p in enumerate(self.pages) if p["id"] == sid), 0) if sid else 0
        self.top = min(self.top, max(0, len(self.pages) - 1))

    def replace_page(self, updated):
        for n, p in enumerate(self.all):
            if p["id"] == updated["id"]:
                self.all[n] = updated
        self.refilter()

    def on_view_tab(self):
        return bool(self.cfg and self.cfg["filters"][self.fidx].get("view"))

    def apply_sort(self):
        """Order the loaded pages (and group them by status) while keeping the selection on the same page."""
        sid = self.pages[self.sel]["id"] if self.pages and self.sel < len(self.pages) else None
        mode = self.SORTS[self.sort_mode][0]
        if self.on_view_tab() and not self.sort_mode and not self.group:  # keep the order the view has in Notion
            self.pages = [p for p in self.all if self.matches(p)]
            self.sel = next((i for i, p in enumerate(self.pages) if p["id"] == sid), 0) if sid else 0
            return
        if mode == "title":
            self.all.sort(key=lambda p: nt.page_title(p).lower())
        elif mode == "status":
            self.all.sort(key=self.status_rank)
        else:
            self.all.sort(key=lambda p: p.get("last_edited_time") or "", reverse=True)
        if self.group:  # stable: keeps the chosen order inside each group
            self.all.sort(key=self.status_rank)
        self.pages = [p for p in self.all if self.matches(p)]
        self.sel = next((i for i, p in enumerate(self.pages) if p["id"] == sid), 0) if sid else 0

    def build_display(self):
        """Rows to draw: ("h", header text) between groups, ("r", index into self.pages) for pages."""
        counts = {}
        for p in self.pages:
            k = nt.prop_text(p["properties"].get(self.cfg["status_property"])) or "No status"
            counts[k] = counts.get(k, 0) + 1
        out, last = [], None
        for i, p in enumerate(self.pages):
            if self.group:
                k = nt.prop_text(p["properties"].get(self.cfg["status_property"])) or "No status"
                if k != last:
                    out.append(("h", "%s %s (%d)" % (self.MARK[self.status_kind(p)], k, counts[k])))
                    last = k
            out.append(("r", i))
        return out

    def load_seen(self):
        try:
            with open(state_file("seen.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_seen(self):
        try:
            os.makedirs(os.path.dirname(state_file("x")), exist_ok=True)
            items = sorted(self.seen.items(), key=lambda kv: kv[1], reverse=True)[:3000]
            with open(state_file("seen.json"), "w", encoding="utf-8") as f:
                json.dump(dict(items), f)
        except OSError:
            pass

    def baseline_seen(self):
        """Pages met for the first time count as seen, so only later edits show the unread dot."""
        new = False
        for p in self.pages:
            if p["id"] not in self.seen and p.get("last_edited_time"):
                self.seen[p["id"]] = p["last_edited_time"]
                new = True
        if new:
            self.save_seen()

    def unread(self, page):
        edited = page.get("last_edited_time") or ""
        return bool(edited) and edited > self.seen.get(page["id"], edited)

    def mark_seen(self, page):
        if page.get("last_edited_time") and self.seen.get(page["id"]) != page["last_edited_time"]:
            self.seen[page["id"]] = page["last_edited_time"]
            self.save_seen()

    # ------------------------------------------------------------ preview pane and auto-refresh
    PREVIEW_MIN_WIDTH = 120

    def wide(self):
        return self.scr.getmaxyx()[1] >= self.PREVIEW_MIN_WIDTH

    def track_selection(self):
        """Note when the selected page changes: the preview starts at its top and waits a moment before fetching."""
        cur = self.pages[self.sel]["id"] if self.pages and self.sel < len(self.pages) else None
        if cur != self.sel_id:
            self.sel_id, self.sel_changed, self.pscroll = cur, time.time(), 0
            if cur is None:
                self.focus = "list"

    def maybe_preview(self):
        """In a wide pane, fetch the body of the selected page once the selection has rested for a moment."""
        if self.job or self.detail or not self.pages or not self.wide() or self.sel >= len(self.pages):
            return
        page = self.pages[self.sel]
        if page["id"] in self.cache or time.time() - self.sel_changed < 0.3:
            return
        pid = page["id"]

        def done(lines):
            self.cache[pid] = lines

        self.start_job("", lambda: self.client.body_lines(pid), done, "preview")

    def auto_refresh(self):
        """Quietly reload the list every minute; rows already on screen are updated in place."""
        if self.job or self.detail or not self.pages or time.time() - self.last_refresh < 60:
            return
        self.last_refresh = time.time()

        def work():
            schema = self.client.discover()
            body = self.build_list_query(schema)
            return schema, body, self.client.query(body)

        def done(r):
            schema, body, res = r
            fresh = res.get("results", [])
            ids = {p["id"] for p in fresh}
            sid = self.pages[self.sel]["id"] if self.sel < len(self.pages) else None
            extra = [p for p in self.all if p["id"] not in ids]
            self.all = fresh + extra
            if len(extra) == 0:
                self.has_more, self.cursor = bool(res.get("has_more")), res.get("cursor")
            self.groups = nt.status_groups(schema, self.cfg["status_property"])
            self.apply_sort()
            if sid:
                self.sel = next((i for i, p in enumerate(self.pages) if p["id"] == sid), self.sel)
            self.baseline_seen()

        self.start_job("", work, done, "silent")

    # ------------------------------------------------------------ drawing
    MARK = {"todo": "○", "progress": "◐", "done": "✓", "": "●"}
    PAIR = {"todo": 1, "progress": 2, "done": 3, "": 0}

    def init_colors(self):
        self.colors = False
        if os.environ.get("NO_COLOR"):  # the markers (▌ ○ ◐ ✓) still carry the meaning
            return
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_YELLOW, -1)
            curses.init_pair(2, curses.COLOR_CYAN, -1)
            curses.init_pair(3, curses.COLOR_GREEN, -1)
            curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_CYAN)  # active tab
            curses.init_pair(5, curses.COLOR_RED, -1)  # urgent
            self.colors = True
        except curses.error:
            pass

    URGENT = ("urgent", "high", "critical", "p0", "p1", "blocker")

    def kind_attr(self, kind):
        """Display attribute for a status kind: to do is quiet, in progress yellow, done green."""
        return {"todo": curses.A_DIM, "progress": self.cp(1), "done": self.cp(3)}.get(kind, 0)

    def cp(self, n):
        return curses.color_pair(n) if self.colors else 0

    def status_cell(self, page):
        """(marker, text, color attr) for the status property of a page."""
        prop = self.cfg["status_property"]
        text = nt.prop_text(page["properties"].get(prop))
        kind = self.status_kind(page)
        return self.MARK[kind], text, self.kind_attr(kind)

    def rule(self, y, w):
        self.scr.addnstr(y, 0, "─" * (w - 1), w - 1, curses.A_DIM)

    CHIPS_MIN_WIDTH = 70

    def kind_of(self, name):
        return nt.group_kind(self.groups.get(name, "")) or nt.guess_kind(name)

    def local_counts(self):
        prop = self.cfg["status_property"]
        counts = {}
        for p in self.all:
            if self.live and self.fold(self.live) not in self.fold(nt.page_title(p)):
                continue
            name = nt.prop_text(p["properties"].get(prop))
            if name:
                counts[name] = counts.get(name, 0) + 1
        return counts

    def status_chips(self):
        """[(status name, count)] for the second header row: every option of the status property in its Notion order,
        counted over the loaded pages (narrowed by the typed text only, so a count does not change when its own chip
        is chosen). While the query itself is narrowed to one status, the counts from before are kept."""
        counts = self.chip_counts if self.server_status else self.local_counts()
        names = sorted(set(self.status_order) | set(counts) | ({self.status_filter} if self.status_filter else set()),
                       key=lambda n: (self.status_order.get(n, 10 ** 6), n))
        return [(n, counts.get(n, 0)) for n in names]

    def set_status_filter(self, name):
        """Show one status ("" for all). When the tab has more pages than are loaded, ask Notion for that status
        so nothing is missed; otherwise narrow the loaded pages at once."""
        if name == self.status_filter:
            return
        self.status_filter = name
        if self.on_view_tab():  # a saved view keeps its own filters: only the loaded pages can be narrowed
            self.refilter()
        elif name and not self.server_status and self.has_more:
            self.chip_counts, self.more_all, self.server_status = self.local_counts(), True, True
            self.refresh_async("Loading %s…" % name)
        elif self.server_status:
            self.server_status = bool(name)
            self.refresh_async("Loading %s…" % name if name else "Loading…")
        else:
            self.refilter()
        self.msg = ("showing %s" % name) if name else "showing every status"

    def cycle_status(self, step):
        """[ and ]: move along the status chips; past the last one (or before the first) the filter is cleared."""
        order = [""] + [n for n, _ in self.status_chips()]
        i = order.index(self.status_filter) if self.status_filter in order else 0
        self.set_status_filter(order[(i + step) % len(order)])

    def draw_chips(self, y, w):
        """Row of status chips with counts; the chosen one is highlighted. Falls back to a rule when it cannot fit."""
        chips = self.status_chips()
        if w < self.CHIPS_MIN_WIDTH or not chips:
            self.rule(y, w)
            return
        more = self.more_all if self.server_status else self.has_more
        total = sum(n for _, n in chips)

        def items(rows):
            return [("", "All %d%s" % (total, "+" if more else ""), curses.A_BOLD)] + [
                (n, "%s %s %d%s" % (self.MARK[self.kind_of(n)], n, c, "+" if more else ""),
                 self.kind_attr(self.kind_of(n)) if c or n == self.status_filter else curses.A_DIM) for n, c in rows]

        def width(its):
            return sum(len(l) + 3 for _, l, _ in its)

        its = items(chips)
        if width(its) > w - 14:  # too wide for every status: leave out the ones with nothing loaded
            its = items([(n, c) for n, c in chips if c or n == self.status_filter])
        x = 1
        for name, label, attr in its:
            label = " %s " % label
            if x + len(label) > w - 3:
                self.scr.addnstr(y, x, "›", 1, curses.A_DIM)
                break
            on = name == self.status_filter
            self.scr.addnstr(y, x, label, len(label), curses.A_REVERSE | curses.A_BOLD if on else attr)
            self.hits.append((y, x, x + len(label), "status", name))
            x += len(label) + 1
        self.scr.addnstr(y, max(x + 1, w - 12), "[ ] status", 10, curses.A_DIM) if x + 12 < w else None

    def draw_tabs(self, w):
        self.scr.addnstr(0, 0, " Notion ", w - 1, curses.A_BOLD)
        extras = ""
        if self.query:
            extras += ' · / "%s"' % self.query
        if self.live:
            extras += ' · typed "%s"' % self.live
        if self.status_filter:
            extras += " · status: %s" % self.status_filter
        if self.sort_mode:
            extras += " · sort: %s" % self.SORTS[self.sort_mode][1]
        if self.group:
            extras += " · grouped"
        right = "%s%d%s pages%s " % (self.spin() + " " if self.visible_job() else "", len(self.pages), "+" if self.has_more else "", extras)
        self.scr.addnstr(0, max(9, w - 1 - len(right)), right[:max(0, w - 10)], len(right), curses.A_DIM)
        # as many tabs as fit between the name and the count, always including the active one
        avail = w - 9 - len(right) - 2
        labels = [" %s %s " % (str(i + 1) if i < 9 else "·", f["name"]) for i, f in enumerate(self.cfg["filters"])]
        start = 0
        while start < self.fidx and sum(len(l) + 1 for l in labels[start:self.fidx + 1]) + 2 > avail:
            start += 1
        x = 9
        if start:
            self.scr.addnstr(0, x, "‹", 1, curses.A_DIM)
            x += 2
        for i in range(start, len(labels)):
            if x + len(labels[i]) + 2 > 9 + avail and i != self.fidx:
                self.scr.addnstr(0, x, "›", 1, curses.A_DIM)
                break
            self.hits.append((0, x, x + len(labels[i]), "tab", i))
            self.scr.addnstr(0, x, labels[i], max(1, w - 1 - x),
                             self.cp(4) | curses.A_BOLD if i == self.fidx and self.colors
                             else curses.A_REVERSE if i == self.fidx else curses.A_DIM)
            x += len(labels[i]) + 1

    BODY_ATTR = {"rule": curses.A_DIM, "quote": curses.A_DIM, "fence": curses.A_DIM}

    def draw_preview(self, x0, width, top, bottom):
        """Right-hand pane of a wide layout: the selected page's title, facts and body."""
        if not self.pages or self.sel >= len(self.pages) or width < 20:
            return
        p = self.pages[self.sel]
        y = top
        for line in textwrap.wrap(nt.clean(nt.page_title(p)), width)[:3]:
            self.scr.addnstr(y, x0, line, width, curses.A_BOLD)
            y += 1
        extra = [c for c in self.cfg["columns"] if c != self.cfg["status_property"]]
        mark, text, attr = self.status_cell(p)
        facts = [("%s %s" % (mark, text), attr)] + [(nt.prop_text(p["properties"].get(c)), curses.A_DIM) for c in extra]
        x = x0
        for f, a_ in [f for f in facts if f[0].strip()] + [("edited " + nt.relative_time(p.get("last_edited_time")), curses.A_DIM)]:
            if x + len(f) >= x0 + width:
                break
            self.scr.addnstr(y, x, f, width, a_)
            x += len(f) + 3
        y += 1
        self.scr.addnstr(y, x0, "─" * width, width, curses.A_DIM)
        y += 1
        body = self.cache.get(p["id"])
        if body is None:
            self.scr.addnstr(y, x0, "Loading…", width, curses.A_DIM)
            return
        kinds = nt.style_lines(body)
        wrapped = []
        for l, kind in zip(body, kinds):
            attr = {"heading": curses.A_BOLD | curses.A_UNDERLINE, "code": self.cp(2), "done": self.cp(3)}.get(kind, self.BODY_ATTR.get(kind, 0))
            for piece in (textwrap.wrap(nt.display_line(l, kind), width) or [""]):
                wrapped.append((piece, attr))
        room = bottom - y
        if len(wrapped) > room:
            room -= 1  # the last line is kept for the scroll cue
        self.pscroll = max(0, min(self.pscroll, max(0, len(wrapped) - room)))
        for piece, attr in wrapped[self.pscroll:self.pscroll + room]:
            self.scr.addnstr(y, x0, piece, width, attr)
            y += 1
        if len(wrapped) > room:  # where we are in the page, and how to scroll it
            mark = "%d%%" % min(100, 100 * (self.pscroll + room) // len(wrapped))
            more = self.pscroll + room < len(wrapped)
            hint = ("↑↓ scroll · Tab back · " if self.focus == "preview"
                    else ("Space ▾ more · " if more else "b ▴ back · ")) + mark
            self.scr.addnstr(bottom - 1, max(x0, x0 + width - len(hint)), hint, min(len(hint), width), curses.A_DIM | curses.A_BOLD)

    def draw_list(self):
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        self.hits = []
        self.draw_tabs(w)
        self.draw_chips(1, w)
        wide = w >= self.PREVIEW_MIN_WIDTH
        tw_total = max(70, int(w * 0.56)) if wide else w  # width of the table; the rest is the preview pane
        # Columns right of the title, in priority order: status, other columns, edited.
        # They are dropped from the right until the title has at least 20 columns.
        extra = [c for c in self.cfg["columns"] if c != self.cfg["status_property"]]
        cols = ([] if self.compact else [("STATUS", 14)]) + [(c.upper(), min(14, max(len(c), 6))) for c in extra] \
            + ([("AGENT", 12)] if any(p["id"] in self.delegations for p in self.pages) else []) + [("EDITED", 6)]
        while cols and tw_total - 4 - sum(cw + 2 for _, cw in cols) < 20:
            cols.pop()
        tw = max(10, tw_total - 4 - sum(cw + 2 for _, cw in cols))
        head = "  " + "TITLE".ljust(tw) + "".join("  " + n.ljust(cw) for n, cw in cols)
        self.scr.addnstr(2, 0, head, tw_total - 1, curses.A_DIM | curses.A_BOLD)
        peek = 4 if (h >= 20 and not wide) else 0  # the selected page in full, since titles get clipped in the table
        rows = h - 6 - peek
        disp = self.build_display()
        sel_d = next((j for j, (t, i) in enumerate(disp) if t == "r" and i == self.sel), 0)
        if sel_d < self.top:
            self.top = max(0, sel_d - (1 if sel_d > 0 and disp[sel_d - 1][0] == "h" else 0))
        if sel_d >= self.top + rows:
            self.top = sel_d - rows + 1
        stale = bool(self.job and self.job["kind"] == "list" and self.pages)
        for r, (kind, i) in enumerate(disp[self.top:self.top + rows]):
            y = 3 + r
            if kind == "h":
                self.scr.addnstr(y, 2, i, tw_total - 3, curses.A_BOLD | curses.A_DIM)
                continue
            pg = self.pages[i]
            on = i == self.sel
            base = curses.A_REVERSE if on else (curses.A_DIM if stale else 0)
            self.scr.addnstr(y, 0, " " * (tw_total - 1), tw_total - 1, base)
            kind_ = self.status_kind(pg)
            bar = self.kind_attr(kind_) if not (on and kind_ == "todo") else 0
            self.scr.addnstr(y, 0, "▌", 1, (curses.A_BOLD if on else base) | bar)
            self.hits.append((y, 0, tw_total, "row", i))
            if pg["id"] in self.marked:
                self.scr.addnstr(y, 1, "+", 1, base | curses.A_BOLD)
            elif self.unread(pg):
                self.scr.addnstr(y, 1, "•", 1, base | self.cp(3) | curses.A_BOLD)
            self.scr.addstr(y, 2, nt.clip(nt.page_title(pg), tw), base | (curses.A_BOLD if on else 0))
            x = 2 + tw
            for n, cw in cols:
                x += 2
                if n == "STATUS":
                    mark, text, attr = self.status_cell(pg)
                    self.scr.addstr(y, x, nt.clip("%s %s" % (mark, text), cw), base | attr)
                elif n == "EDITED":
                    self.scr.addnstr(y, x, nt.relative_time(pg.get("last_edited_time")), cw, base | curses.A_DIM)
                elif n == "AGENT":
                    cell = self.agent_cell(pg)
                    if cell:
                        self.scr.addstr(y, x, nt.clip(cell[0], cw), base | cell[1])
                else:
                    key = next(c for c in extra if c.upper() == n)
                    text = nt.prop_text(pg["properties"].get(key))
                    hot = self.cp(5) | curses.A_BOLD if text.lower() in self.URGENT else 0
                    self.scr.addstr(y, x, nt.clip(text, cw), base | hot)
                x += cw
        if not self.pages and self.job:
            for r in range(min(rows, 8)):  # skeleton rows while the first page loads
                bar = "░" * (14 + (r * 7) % 17)
                self.scr.addnstr(3 + r, 2, bar, tw, curses.A_DIM)
                self.scr.addnstr(3 + r, 2 + tw + 2, "░░░░░░░░", 8, curses.A_DIM)
        elif not self.pages:
            self.scr.addnstr(4, 2, "No pages match this filter.", tw_total - 3, curses.A_DIM)
        if wide:
            for y in range(2, h - 3):
                self.scr.addnstr(y, tw_total, "┃" if self.focus == "preview" else "│", 1, self.cp(2) | curses.A_BOLD if self.focus == "preview" else curses.A_DIM)
            self.draw_preview(tw_total + 2, w - tw_total - 3, 3, h - 3)
        if peek and self.pages:
            self.rule(h - 3 - peek, w)
            p = self.pages[self.sel]
            for i, line in enumerate(textwrap.wrap(nt.clean(nt.page_title(p)), max(w - 4, 10))[:2]):
                self.scr.addnstr(h - 2 - peek + i, 2, line, w - 3, curses.A_BOLD)
            mark, text, attr = self.status_cell(p)
            facts = [text] + [nt.prop_text(p["properties"].get(c)) for c in extra]
            facts = [f for f in facts if f] + ["edited " + nt.relative_time(p.get("last_edited_time"))]
            cell = self.agent_cell(p)
            if cell:
                facts.append("agent " + cell[0])
            self.scr.addnstr(h - 4, 2, "  ·  ".join(facts), w - 3, curses.A_DIM)
        self.rule(h - 3, w)
        self.footer([("↑↓/PgUp PgDn", "scroll"), ("Tab", "back to the list"), ("⏎", "open")] if self.focus == "preview" else
                    [("⏎", "open"), ("s", "status"), ("d", "delegate"), ("←→", "tabs"), ("/", "search")]
                    + ([("Space/b", "scroll preview")] if wide else []) + [("e", "edit"), ("z", "compact"), ("n", "new"), ("?", "more")])

    def draw_detail(self):
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        page = self.detail["page"]
        mark, stext, sattr = self.status_cell(page)
        right = "%s %s" % (mark, stext)
        self.scr.addnstr(0, 0, " ‹ " + nt.page_title(page), w - len(right) - 3, curses.A_BOLD)
        if len(right) + 2 < w:
            self.scr.addnstr(0, w - len(right) - 1, right, len(right), sattr | curses.A_BOLD)
        self.rule(1, w)
        # property block in two columns, then the link, then the body
        props = [kv for kv in nt.page_properties(page) if kv[0] != self.cfg["status_property"]]
        half = (w - 2) // 2 if w >= 60 else w - 2
        lines = []
        step = 2 if w >= 60 else 1
        for i in range(0, len(props), step):
            row = ""
            for k, v in props[i:i + step]:
                row += ("%-9s %s" % (k[:9], v)).ljust(half)[:half]
            lines.append((" " + row, 0))
        lines += [(" " + page.get("url", ""), curses.A_DIM), ("", 0), ("─" * (w - 1), curses.A_DIM)]
        body = self.detail["body"].split("\n")
        for l, kind in zip(body, nt.style_lines(body)):
            attr = {"heading": curses.A_BOLD | curses.A_UNDERLINE, "rule": curses.A_DIM,
                    "quote": curses.A_DIM, "fence": curses.A_DIM, "code": self.cp(2),
                    "done": self.cp(3), "todo": 0}.get(kind, 0)
            text = nt.display_line(l, kind)
            wrapped = [text[i:i + w - 3] for i in range(0, max(len(text), 1), max(w - 3, 1))]
            lines += [(" " + x, attr) for x in wrapped]
        comments = self.detail.get("comments") or []
        if comments:
            lines += [("", 0), ("─" * (w - 1), curses.A_DIM), (" Comments (%d)" % len(comments), curses.A_BOLD)]
            for c_ in comments:
                head = "%s · %s" % (c_["author"], nt.relative_time(c_["when"]))
                lines.append((" " + head, self.cp(1) | curses.A_BOLD))
                for para in (c_["text"].split("\n") or [""]):
                    for piece in (textwrap.wrap(para, max(w - 5, 10)) or [""]):
                        lines.append(("   " + piece, 0))
        view = h - 5
        self.dscroll = max(0, min(self.dscroll, max(0, len(lines) - view)))
        for r, (l, attr) in enumerate(lines[self.dscroll:self.dscroll + view]):
            self.scr.addnstr(2 + r, 0, l, w - 1, attr)
        self.rule(h - 3, w)
        if len(lines) > view:
            ind = "%d%%" % min(100, 100 * (self.dscroll + view) // len(lines))
            self.scr.addnstr(h - 3, max(0, w - len(ind) - 2), ind, len(ind), curses.A_DIM)
        self.footer([("s", "status"), ("e", "edit"), ("c", "comment"), ("d", "delegate"), ("o", "browser"), ("q", "back"), ("?", "more")])

    def footer(self, hints):
        h, w = self.scr.getmaxyx()
        ctx = self.cfg["filters"][self.fidx]["name"] if self.cfg else ""
        # keep the filter name only when the hints still have room
        text = nt.fit_hints(hints, w - 2 - (len(ctx) + 3 if ctx else 0))
        if len(text) < len(nt.fit_hints(hints, w - 2)):
            ctx = ""
            text = nt.fit_hints(hints, w - 2)
        note = "%s %s" % (self.spin(), self.job["label"]) if self.visible_job() else self.msg
        if note:
            self.scr.addnstr(h - 2, 1, note, w - 2, curses.A_BOLD if not self.job else curses.A_DIM)
            hint_row = h - 1
        else:
            hint_row = h - 2
        self.scr.addnstr(hint_row, 1, text, w - 2, curses.A_DIM)
        if ctx:
            self.scr.addnstr(hint_row, w - len(ctx) - 2, ctx, len(ctx), curses.A_DIM | curses.A_BOLD)

    # ------------------------------------------------------------ popups
    def popup(self, title, items, start=0):
        """Pick one of `items`; returns an index or None. 1-9 pick directly."""
        sel = start
        while True:
            h, w = self.scr.getmaxyx()
            items = [nt.clean(i) for i in items]
            bw = min(w - 2, max([nt.cell_width(title) + 6] + [nt.cell_width(i) + 7 for i in items]))
            bh = min(h - 2, len(items) + 2)
            y0, x0 = (h - bh) // 2, (w - bw) // 2
            win = curses.newwin(bh, bw, y0, x0)
            win.keypad(True)
            win.box()
            win.addnstr(0, 2, " %s " % title, bw - 4, curses.A_BOLD)
            first = max(0, min(sel - (bh - 3), len(items) - (bh - 2)))
            for r, it in enumerate(items[first:first + bh - 2]):
                i = first + r
                tag = "%d " % (i + 1) if i < 9 else "  "
                row = tag + nt.pad(it, bw - 2 - len(tag))
                win.addstr(1 + r, 1, row, curses.A_REVERSE if i == sel else 0)
            win.refresh()
            k = win.getch()  # not self.scr.getch(): that would repaint the background over the popup
            if k in (27, ord("q")):
                return None
            if k in (curses.KEY_DOWN, ord("j")):
                sel = min(sel + 1, len(items) - 1)
            elif k in (curses.KEY_UP, ord("k")):
                sel = max(sel - 1, 0)
            elif k in (10, 13, curses.KEY_ENTER):
                return sel
            elif ord("1") <= k <= ord("9") and k - ord("1") < len(items):
                return k - ord("1")

    def ask(self, label, initial="", secret=False):
        """One-line text input; Esc cancels (returns None). `secret` echoes *."""
        buf = initial
        curses.curs_set(1)
        try:
            while True:
                h, w = self.scr.getmaxyx()
                shown = "*" * len(buf) if secret else buf
                self.scr.addnstr(h - 1, 0, (label + shown).ljust(w - 1), w - 1)
                self.scr.move(h - 1, min(len(label) + len(shown), w - 1))
                k = self.scr.get_wch()
                if k == "\x1b":
                    return None
                if k in ("\n", "\r"):
                    return buf
                if k in ("\x7f", "\b") or k == curses.KEY_BACKSPACE:
                    buf = buf[:-1]
                elif isinstance(k, str) and k.isprintable():
                    buf += k
        finally:
            curses.curs_set(0)

    def ask_paste(self, label, at=None):
        """Input for a pasted address: a paste that wraps over several lines is read as one piece
        (line breaks are dropped), and it is submitted once nothing more arrives. Esc cancels.
        `at` = (row, col, width) types inside a box already on the screen; otherwise on the last line."""
        buf, pending = "", False
        curses.curs_set(1)
        try:
            while True:
                h, w = self.scr.getmaxyx()
                if at:
                    row, col, width = at
                    shown = buf[-(width - 1):] if len(buf) > width - 1 else buf
                    self.scr.addnstr(row, col, shown.ljust(width), width)
                    self.scr.move(row, min(col + len(shown), col + width - 1))
                else:
                    shown = buf[-(w - len(label) - 2):] if len(buf) > w - len(label) - 2 else buf
                    self.scr.addnstr(h - 1, 0, (label + shown).ljust(w - 1), w - 1)
                    self.scr.move(h - 1, min(len(label) + len(shown), w - 1))
                self.scr.timeout(300 if pending else -1)
                try:
                    k = self.scr.get_wch()
                except curses.error:  # silence after a line break: the paste is complete
                    return buf
                if k == "\x1b":
                    return None
                if k in ("\n", "\r"):
                    pending = bool(buf)
                    continue
                pending = False
                if k in ("\x7f", "\b") or k == curses.KEY_BACKSPACE:
                    buf = buf[:-1]
                elif isinstance(k, str) and k.isprintable() and not k.isspace():
                    buf += k
        finally:
            self.scr.timeout(-1)
            curses.curs_set(0)

    def confirm(self, text):
        h, w = self.scr.getmaxyx()
        self.scr.addnstr(h - 1, 0, (text + " [y/N]").ljust(w - 1), w - 1, curses.A_BOLD)
        return self.scr.getch() in (ord("y"), ord("Y"))

    # ------------------------------------------------------------ actions
    def change_status(self):
        """s: set the status of the current page, or of every marked page (x marks) with one confirm."""
        marked = [p for p in self.all if p["id"] in self.marked] if not self.detail else []
        page = self.current()
        if not page and not marked:
            return
        prop = self.cfg["status_property"]
        kind, options = nt.status_options(self.client.discover(), prop)
        many = "%d pages" % len(marked) if len(marked) > 1 else ""
        i = self.popup("Set %s%s" % (prop, " on " + many if many else ""), options)
        if i is None:
            return
        if not self.confirm("Set %s to '%s'%s?" % (prop, options[i], " on " + many if many else "")):
            self.msg = "cancelled"
            return
        if marked:
            done = 0
            for n, p in enumerate(marked):
                self.note("Updating %d of %d…" % (n + 1, len(marked)))
                self.replace_page(self.client.set_status(p["id"], prop, kind, options[i]))
                self.marked.discard(p["id"])
                done += 1
            self.msg = "%s set to %s on %d page%s" % (prop, options[i], done, "" if done == 1 else "s")
            return
        self.note("Updating…")
        updated = self.client.set_status(page["id"], prop, kind, options[i])
        self.replace_page(updated)
        if self.detail:
            self.open_page(updated)
        self.msg = "%s set to %s · press d to delegate it" % (prop, options[i])

    def advance_status(self):
        """>: move the current page to the next status of the property (To do, In progress, Done…)."""
        page = self.current()
        if not page:
            return
        prop = self.cfg["status_property"]
        kind, options = nt.status_options(self.client.discover(), prop)
        now = nt.prop_text(page["properties"].get(prop))
        i = options.index(now) + 1 if now in options else 0
        if i >= len(options):
            self.msg = "already at the last status (%s)" % now
            return
        if not self.advance_ok and not self.confirm("Move to '%s'? (asked once, then > just does it)" % options[i]):
            self.msg = "cancelled"
            return
        self.advance_ok = True
        self.note("Updating…")
        updated = self.client.set_status(page["id"], prop, kind, options[i])
        self.replace_page(updated)
        if self.detail:
            self.open_page(updated)
        self.msg = "%s: %s → %s" % (nt.page_title(page)[:30], now or "none", options[i])

    def toggle_mark(self):
        """x: mark or unmark the current page for a bulk status change, then move down."""
        page = self.current()
        if not page:
            return
        self.marked ^= {page["id"]}
        self.sel = min(self.sel + 1, len(self.pages) - 1)
        self.msg = "%d marked: s sets their status, Esc clears" % len(self.marked) if self.marked else "no pages marked"

    def build_prompt(self):
        """(page, rendered prompt) for the current page, fetching the body if needed."""
        page = self.current()
        if not page:
            return None, None
        if self.detail:
            body = self.detail["body"]
        else:
            if page["id"] not in self.cache:  # the preview usually has it already; otherwise say so in the footer
                self.note("Reading the page…")
                page = self.client.get_page(page["id"])
                self.cache[page["id"]] = self.client.body_lines(page["id"])
            body = "\n".join(self.cache[page["id"]])
        dcfg = self.cfg["delegate"]
        return page, nt.render_prompt(dcfg["prompt"], page, body, dcfg["max_body_chars"])

    # ------------------------------------------------------------ edit, comment, create, switch database
    EDITABLE = ("select", "status", "number", "rich_text", "url", "email", "phone_number", "checkbox")

    def edit_property(self):
        """e: change one property of the selected page (asks to confirm; the write goes through Notion's MCP)."""
        page = self.current()
        if not page:
            return
        schema = self.client.discover()
        names = [n for n, pr in schema["properties"].items() if pr["type"] in self.EDITABLE]
        if not names:
            self.msg = "no editable properties"
            return
        i = self.popup("Edit property", ["%s (%s)" % (n, schema["properties"][n]["type"]) for n in names])
        if i is None:
            return
        name, kind = names[i], schema["properties"][names[i]]["type"]
        now = nt.prop_text(page["properties"].get(name))
        if kind in ("select", "status"):
            opts = [o["name"] for o in schema["properties"][name][kind].get("options", [])]
            j = self.popup("Set %s" % name, opts + (["(clear)"] if kind == "select" else []), opts.index(now) if now in opts else 0)
            if j is None:
                return
            value, shown = (opts[j], opts[j]) if j < len(opts) else (None, "(empty)")
        elif kind == "checkbox":
            j = self.popup("Set %s" % name, ["yes", "no"])
            if j is None:
                return
            value, shown = ("__YES__", "yes") if j == 0 else ("__NO__", "no")
        else:
            text = self.ask("%s: " % name, now)
            if text is None:
                return
            if kind == "number":
                try:
                    value = float(text) if "." in text else int(text)
                except ValueError:
                    self.msg = "%r is not a number" % text
                    return
            else:
                value = text
            shown = text or "(empty)"
        if not self.confirm("Set %s to '%s'?" % (name, nt.clip(shown, 40))):
            self.msg = "cancelled"
            return
        self.busy("Updating…")
        updated = self.client.set_property(page["id"], name, value)
        self.replace_page(updated)
        if self.detail:
            self.open_page(updated)
        self.msg = "%s set to %s" % (name, shown)

    def comment(self):
        """c (in a page): add a comment."""
        if not self.detail:
            return
        text = self.ask("comment: ")
        if not text or not text.strip():
            return
        if not self.confirm("Post this comment?"):
            self.msg = "cancelled"
            return
        self.busy("Posting…")
        pid = self.detail["page"]["id"]
        self.client.add_comment(pid, text.strip())
        self.detail["comments"] = self.client.comments(pid)
        self.msg = "comment posted"

    def new_page(self):
        """n: create a page in this database (a title and, if you like, a status)."""
        schema = self.client.discover()
        title = self.ask("new page title: ")
        if not title or not title.strip():
            return
        props, shown = {}, ""
        prop = self.cfg["status_property"]
        if prop in schema["properties"] and schema["properties"][prop]["type"] in ("status", "select"):
            kind, opts = nt.status_options(schema, prop)
            j = self.popup("Status of the new page", ["(Notion's default)"] + opts)
            if j is None:
                return
            if j:
                props[prop], shown = opts[j - 1], " · %s" % opts[j - 1]
        if not self.confirm("Create '%s'%s?" % (nt.clip(title.strip(), 40), shown)):
            self.msg = "cancelled"
            return
        self.busy("Creating…")
        if not self.title_prop:
            self.title_prop = nt.title_property_name(schema.get("properties", {}))
        page = self.client.create_page(self.title_prop, title.strip(), props)
        self.all.insert(0, page)
        self.sort_mode, self.group = 0, False
        self.refilter()
        self.sel = 0
        self.msg = "created '%s'" % nt.clip(title.strip(), 40)

    def database_files(self):
        d = os.path.join(os.path.dirname(cfgmod.config_path()), "databases")
        return d, sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".toml")) if os.path.isdir(d) else []

    @staticmethod
    def database_title(path):
        """The database name written as a comment right under [notion] by the setup."""
        try:
            with open(path, encoding="utf-8") as f:
                inside = False
                for line in f:
                    if line.strip() == "[notion]":
                        inside = True
                    elif inside and line.startswith("# "):
                        return line[2:].strip()
                    elif inside and line.strip() and not line.startswith("#"):
                        break
        except OSError:
            pass
        return os.path.splitext(os.path.basename(path))[0]

    def switch_database(self):
        """D: switch between the databases you have set up (each keeps its own tabs and settings)."""
        import shutil as _sh
        d, files = self.database_files()
        os.makedirs(d, exist_ok=True)
        current = os.path.join(d, "%s.toml" % self.cfg["data_source_id"])
        _sh.copyfile(cfgmod.config_path(), current)  # remember this one before leaving it
        d, files = self.database_files()
        others = [f for f in files if f != current]
        items = ["● " + (self.source_title() if self.source_title() != "(unnamed)" else self.database_title(current))] + [self.database_title(f) for f in others] + ["+ add another database…"]
        i = self.popup("Databases", items)
        if i is None or i == 0:
            return
        if i <= len(others):
            _sh.copyfile(others[i - 1], cfgmod.config_path())
        elif not self.pick_and_save(confirm=False):
            return
        self.load_config()
        self.all, self.pages, self.cache, self.detail = [], [], {}, None
        self.refresh_async("Loading %s…" % self.database_title(cfgmod.config_path()))

    # ------------------------------------------------------------ delegation tracking
    def load_delegations(self):
        try:
            with open(state_file("delegations.json"), encoding="utf-8") as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if not v.get("done")}
        except (OSError, ValueError):
            return {}

    def save_delegations(self):
        try:
            os.makedirs(os.path.dirname(state_file("x")), exist_ok=True)
            with open(state_file("delegations.json"), "w", encoding="utf-8") as f:
                json.dump(self.delegations, f)
        except OSError:
            pass

    def track(self, page, pane, agent):
        self.delegations[page["id"]] = {"pane": pane, "agent": agent, "title": nt.page_title(page),
                                         "seen_working": False, "done": False, "status": "sent"}
        self.next_agent_poll = 0
        self.save_delegations()

    def agent_cell(self, page):
        """(text, attr) for the AGENT column of a page, or None when it was never delegated."""
        d = self.delegations.get(page["id"])
        if not d:
            return None
        if d.get("done"):
            return "✔ %s" % d["agent"], self.cp(3)
        working = d.get("status") == "working"
        return "%s %s" % ("◉" if working else "○", d["agent"]), self.cp(2) if working else curses.A_DIM

    def poll_agents(self):
        """Every few seconds ask herdr how the delegated agents are doing (never while something else loads)."""
        active = [d for d in self.delegations.values() if not d.get("done")]
        if not active or self.job or time.time() < self.next_agent_poll:
            return
        self.next_agent_poll = time.time() + 5
        self.start_job("", dg.list_agents, self.on_agents, "agents")

    def on_agents(self, agents):
        status = {a["pane_id"]: a["status"] for a in agents}
        for pid, d in self.delegations.items():
            if d.get("done"):
                continue
            now = status.get(d["pane"])
            if now is None:  # the pane is gone
                d["done"] = True
                continue
            d["status"] = now
            if now == "working":
                d["seen_working"] = True
            elif d["seen_working"] and now in ("idle", "done"):
                d["done"] = True
                self.finished.append(pid)
        self.save_delegations()

    def offer_review(self):
        """The agent finished a delegated page: offer to move it on (to the option named like 'review')."""
        pid = self.finished.pop(0)
        d = self.delegations.get(pid)
        schema = self.client.discover()
        kind, options = nt.status_options(schema, self.cfg["status_property"])
        target = next((o for o in options if "review" in o.lower()), "")
        page = next((p for p in self.pages if p["id"] == pid), None)
        if not d or not options:
            return
        if not target:
            i = self.popup("%s finished. Move it to" % d["agent"], options + ["leave as it is"])
            if i is None or i >= len(options):
                return
            target = options[i]
        elif not self.confirm("%s finished '%s'. Set %s to '%s'?" % (d["agent"], nt.clip(d["title"], 30), self.cfg["status_property"], target)):
            return
        self.busy("Updating…")
        updated = self.client.set_status(pid, self.cfg["status_property"], kind, target)
        self.replace_page(updated)
        self.msg = "%s set to %s" % (self.cfg["status_property"], target)

    def delegate(self):
        page, prompt = self.build_prompt()
        if not page:
            return
        dcfg = self.cfg["delegate"]
        (self.draw_detail if self.detail else self.draw_list)()  # put the screen back behind the popup
        self.scr.refresh()
        agents = dg.list_agents()
        items = [dg.agent_label(a) for a in agents] + ["+ start new agent…"]
        i = self.popup("Delegate to", items)
        if i is None:
            return
        if i < len(agents):
            self.note("Sending prompt…")
            dg.send_prompt(agents[i]["pane_id"], prompt)
            self.track(page, agents[i]["pane_id"], agents[i]["name"] or agents[i]["agent"])
            self.msg = "sent to %s" % agents[i]["name"]
            return
        kinds = dcfg["agents"]
        j = self.popup("Agent type", [a["name"] for a in kinds])
        if j is None:
            return
        cwds = dg.unique_cwds(agents, dcfg["default_cwd"])
        k = self.popup("Working directory", cwds + ["type path…"])
        if k is None:
            return
        cwd = cwds[k] if k < len(cwds) else self.ask("cwd: ", os.getcwd())
        if not cwd:
            return
        cwd = os.path.expanduser(os.path.expandvars(cwd))
        self.note("Starting %s…" % kinds[j]["name"])
        pane = dg.start_agent(kinds[j]["command"], cwd, nt.page_title(page)[:30], dcfg,
                              os.environ.get("HERDR_WORKSPACE_ID"))
        dg.send_prompt(pane, prompt)
        self.track(page, pane, kinds[j]["name"])
        self.msg = "started %s and sent prompt" % kinds[j]["name"]

    def check_connection(self):
        """Who is signed in, and can the configured database be read?"""
        st_ = {"who": "", "signed_in": False, "db": "", "db_ok": False, "login": oauth.load()}
        try:
            me = self.client.whoami()
            st_.update(who="%s%s" % (me["workspace"] or "Notion", " · " + me["user"] if me["user"] else ""), signed_in=True)
        except (nt.NotionError, oauth.OAuthError) as e:
            st_["who"] = str(e)
        if st_["signed_in"]:
            try:
                self.client.discover()
                st_.update(db=self.source_title(), db_ok=True)
            except nt.NotionError:
                st_["db"] = "the configured database cannot be read with this account"
        return st_

    MENU = ["Sign in with the browser", "Pick another database", "Change the status property",
            "Reset the filter tabs (Mine, Current sprint…)", "Sign out", "Reconnect (check again)"]

    @staticmethod
    def tilde(path):
        home = os.path.expanduser("~")
        return "~" + path[len(home):] if path.startswith(home) else path

    LOCKED_MENU = ["Sign in with the browser", "Quit"]

    def connection(self, reason=""):
        """Connection screen: status panel plus a menu. Opened with S, and by itself on a connection problem.
        While nobody is signed in it only offers to sign in or quit: the pages stay out of reach."""
        sel = 0
        self.reconnect = ""
        locked = not oauth.load()
        if not locked:
            self.busy("Checking the connection…")
            status = self.check_connection()
        else:
            status = {"who": "not signed in", "signed_in": False, "db": "", "db_ok": False, "login": None}
            reason = reason or "You are signed out. Sign in to see your pages."
        while True:
            menu = self.LOCKED_MENU if locked else self.MENU
            h, w = self.scr.getmaxyx()
            self.scr.erase()
            ok = status["signed_in"] and status["db_ok"]
            rows = [("Notion connection", None), ("", None)]
            if reason:
                rows += [("Problem:", reason), ("", None)]
            rows += [
                ("Status:", "✔ connected" if ok else "✘ not connected"),
                ("Signed in:", ("✔ %s" % status["who"]) if status["signed_in"] else "✘ %s" % status["who"]),
                ("Database:", ("✔ %s" % status["db"]) if status["db_ok"] else "✘ %s" % (status["db"] or "unknown")),
                ("Status property:", self.cfg["status_property"] if self.cfg else "(none)"),
                ("Server:", oauth.MCP_HOST),
                ("Config:", self.tilde(cfgmod.config_path())), ("", None)]
            y = 1
            for label, value in rows:
                if y >= h - len(menu) - 3:
                    break
                if value is None:
                    self.scr.addnstr(y, 2, label, w - 4, curses.A_BOLD)
                else:
                    self.scr.addnstr(y, 2, label.ljust(18), 18, curses.A_BOLD)
                    self.scr.addnstr(y, 20, value, max(w - 22, 1))
                y += 1
            for i, item in enumerate(menu):
                line = "%s %d. %s" % ("❯" if i == sel else " ", i + 1, item)
                self.scr.addnstr(y + i, 2, line, w - 4, curses.A_REVERSE if i == sel else 0)
            hint = "↑↓ move · Enter or 1-%d choose · Esc %s" % (len(menu), "quits" if locked else "close")
            self.scr.addnstr(min(y + len(menu) + 1, h - 1), 2, hint, w - 4, curses.A_DIM)
            self.scr.refresh()
            k = self.scr.getch()
            if k in (27, ord("q")):
                if locked:
                    self.want_quit = True
                elif not ok:
                    self.msg = "not connected: press S to fix it"
                return
            if k in (curses.KEY_DOWN, ord("j")):
                sel = min(sel + 1, len(menu) - 1)
                continue
            if k in (curses.KEY_UP, ord("k")):
                sel = max(sel - 1, 0)
                continue
            if ord("1") <= k <= ord("0") + len(menu):
                sel = k - ord("1")
            elif k not in (10, 13, curses.KEY_ENTER):
                continue
            self.msg = ""
            if locked:
                if sel == 1:
                    self.want_quit = True
                    return
                self.guard(self.sign_in)
                if oauth.load():
                    return
                reason = self.msg if self.msg else reason
                continue
            action = (self.sign_in, self.change_database, self.change_status_property, self.reset_filters,
                      self.clear_sign_in, lambda: None)[sel]
            self.guard(action)
            if self.want_quit or (sel == 4 and (not oauth.load() or self.msg == "signed in again")):
                return
            reason = self.msg if self.msg and self.connection_problem(self.msg) else ""
            self.reconnect = ""
            self.busy("Checking the connection…")
            status = self.check_connection()
            if status["signed_in"] and status["db_ok"]:
                reason = ""
                self.guard(self.refresh)
                return

    def suggested_tabs(self, client, schema, status_prop):
        """Tabs for this database: Mine (through the same Assignee link the Notion boards use), sprint, open, all, and its views."""
        views, me_page = [], None
        try:
            views = client.views()
        except (nt.NotionError, oauth.OAuthError):
            pass
        try:
            rel = st.assignee_relation(schema)
            if rel:
                url = client.find_person_page(rel, client.whoami()["user"])
                me_page = (rel, url) if url else None
        except (nt.NotionError, oauth.OAuthError):
            pass
        return st.suggest_filters(schema, status_prop, views, me_page)

    def reset_filters(self):
        """Replace the filter tabs with the ones this database supports: Mine first, then Current sprint, Open, All."""
        self.busy("Reading your views and your person page…")
        flt = self.suggested_tabs(self.client, self.client.discover(), self.cfg["status_property"])
        if not self.confirm("Replace the filter tabs with %s?" % ", ".join(f["name"] for f in flt)):
            return
        with open(cfgmod.config_path(), encoding="utf-8") as f:
            text = f.read()
        with open(cfgmod.config_path(), "w", encoding="utf-8") as f:
            f.write(st.replace_filters(text, flt))
        self.load_config()
        self.refresh()
        self.msg = "filter tabs: %s" % ", ".join(f["name"] for f in flt)

    def clear_sign_in(self):
        """Sign out: revoke the login at Notion, drop what is loaded, and offer to sign in again."""
        if not oauth.load():
            self.msg = "was not signed in"
            return
        if not self.confirm("Sign out of Notion? The list closes until you sign in again."):
            return
        self.busy("Signing out…")
        oauth.logout()
        self.all, self.pages, self.cache, self.detail = [], [], {}, None
        self.has_more, self.cursor, self.job = False, None, None
        self.client = mb.McpClient(self.cfg["data_source_id"]) if self.cfg else self.client
        self.client.want = set(self.cfg["columns"]) if self.cfg else set()
        if self.require_sign_in("You are signed out. The login was revoked at Notion and removed from this machine."):
            self.msg = "signed in again"

    def require_sign_in(self, text):
        """The pages are only shown while signed in: otherwise the connection screen opens, locked to
        'sign in' or 'quit'. Returns True once signed in."""
        while not oauth.load():
            self.connection(text)
            if self.want_quit:
                return False
        self.refresh_async("Loading your pages…")
        self.splash()  # plays again after signing in
        return True

    def settings(self):
        self.connection()

    def connect_screen(self):
        self.connection(self.reconnect)

    def source_title(self):
        try:
            t = "".join(x.get("plain_text", "") for x in (self.client.discover().get("title") or []))
        except nt.NotionError:
            t = ""
        return t or "(unnamed)"

    def write_config_value(self, key, value):
        """Replace `key = ...` inside config.toml, keeping everything else (filters, prompt) as it is."""
        path = cfgmod.config_path()
        with open(path, encoding="utf-8") as f:
            text = f.read()
        line = "%s = %s" % (key, st.toml_value(value))
        if re.search(r"(?m)^%s\s*=.*$" % re.escape(key), text):
            text = re.sub(r"(?m)^%s\s*=.*$" % re.escape(key), lambda m: line, text, count=1)
        else:
            text = text.replace("[notion]\n", "[notion]\n" + line + "\n", 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def change_status_property(self):
        schema = self.client.discover()
        options = st.status_candidates(schema)
        if not options:
            self.msg = "no status or select property in this database"
            return
        j = self.popup("Which property is the status?", [st.property_label(schema, o) for o in options])
        if j is None:
            return
        self.write_config_value("status_property", options[j])
        self.load_config()
        self.refresh()
        self.msg = "status property is now %s" % options[j]

    def change_database(self):
        if self.pick_and_save(confirm=True):
            self.load_config()
            self.refresh()

    def link(self, y, x, text, url):
        """Make already-drawn text a clickable terminal hyperlink (OSC 8) without disturbing curses."""
        sys.stdout.write("\x1b7\x1b[%d;%dH\x1b]8;;%s\x1b\\%s\x1b]8;;\x1b\\\x1b8" % (y + 1, x + 1, url, text))
        sys.stdout.flush()

    def copy_to_clipboard(self, text):
        """Copy on this machine (pbcopy and friends) and, through the terminal (OSC 52), on the machine you are sitting at."""
        import base64
        try:
            sys.stdout.write("\x1b]52;c;%s\x07" % base64.b64encode(text.encode()).decode())
            sys.stdout.flush()
        except OSError:
            pass
        argv = nt.clipboard_cmd(shutil.which)
        if argv:
            subprocess.run(argv, input=text.encode(), check=False)

    def draw_signin(self, flow, note, boxed):
        """The sign-in screen. Returns (row, col, width) of the paste box."""
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        x = 2
        inner = max(w - 8, 10)
        url = [flow.url[i:i + inner] for i in range(0, len(flow.url), inner)]
        roomy = h >= len(url) + 20
        y = 1
        self.scr.addnstr(y, x, "Sign in to Notion", w - 3, curses.A_BOLD)
        self.scr.addnstr(y + 1, x, "─" * min(w - 4, 64), w - 3, curses.A_DIM)
        y += 3
        self.scr.addnstr(y, x, "1  Open this link in your browser", w - 3, curses.A_BOLD)
        y += 1
        link_rows = []
        for piece in url:
            self.scr.addnstr(y, x + 3, piece, w - 6, self.cp(2))
            link_rows.append((y, x + 3, piece))
            y += 1
        self.scr.addnstr(y, x + 3, "Cmd/Ctrl+click it · o opens it here · c copies it", w - 6, curses.A_DIM)
        y += 2
        if roomy:
            self.scr.addnstr(y, x, "2  Choose a workspace and approve", w - 3, curses.A_BOLD)
            y += 2
        self.scr.addnstr(y, x, "3  Finish", w - 3, curses.A_BOLD)
        y += 1
        for line in ("Browser on this machine: nothing to do, this screen carries on by itself.",
                     "Browser on another machine: it ends on a page that will not load.",
                     "Copy that page's address and paste it in the box (press p first):"):
            self.scr.addnstr(y, x + 3, line, w - 6, curses.A_DIM if "another" not in line else 0)
            y += 1
        y += 1
        bw = max(min(w - 8, 90), 20)
        attr = curses.A_BOLD if boxed else 0
        self.scr.addnstr(y, x + 3, "╭" + "─" * (bw - 2) + "╮", bw, attr)
        self.scr.addnstr(y + 1, x + 3, "│", 1, attr)
        self.scr.addnstr(y + 1, x + 3 + bw - 1, "│", 1, attr)
        if not boxed:
            self.scr.addnstr(y + 1, x + 5, "press p, then paste the address here", bw - 4, curses.A_DIM)
        self.scr.addnstr(y + 2, x + 3, "╰" + "─" * (bw - 2) + "╯", bw, attr)
        box = (y + 1, x + 5, bw - 4)
        y += 4
        if note and y < h - 1:
            self.scr.addnstr(y, x + 3, note, w - 6, curses.A_BOLD)
        self.scr.addnstr(h - 1, 0, " p paste address   o open link   c copy link   Esc cancel".ljust(w - 1), w - 1, curses.A_REVERSE)
        self.scr.refresh()
        for ly, lx, text in link_rows:  # every piece of the long link is clickable and points at the whole link
            self.link(ly, lx, text, flow.url)
        return box

    def sign_in(self):
        """Sign in with the browser, inside the pane. The page to open is Notion's own sign-in page, so it works
        from any machine. When the browser runs on this machine the pane carries on by itself; otherwise the
        browser ends on a page that will not load, and the box takes that page's address."""
        flow = oauth.Flow()
        try:
            webbrowser.open(flow.url)
        except Exception:
            pass
        res = None
        note = ""
        self.scr.timeout(300)
        try:
            end = time.time() + 300
            while time.time() < end:
                res = flow.poll()
                if res:
                    break
                box = self.draw_signin(flow, note, False)
                k = self.scr.getch()
                if k == 27:
                    flow.close()
                    self.msg = "sign-in cancelled"
                    return
                if k == ord("o"):
                    webbrowser.open(flow.url)
                    note = "opened in the browser on this machine"
                elif k == ord("c"):
                    self.copy_to_clipboard(flow.url)
                    note = "link copied: paste it in your browser's address bar"
                elif k == ord("p"):
                    box = self.draw_signin(flow, "", True)
                    pasted = self.ask_paste("", at=box)
                    self.scr.timeout(300)
                    if pasted:
                        got = flow.parse(pasted)
                        if "code" in got or "error" in got:
                            res = got
                            break
                        note = ("That is the sign-in link. Open it, approve, then paste the address the browser ends on."
                                if "code_challenge" in pasted else
                                "No code in that address. Paste the whole address the browser ended on (it starts with http://127.0.0.1).")
            else:
                flow.close()
                raise oauth.OAuthError("timed out waiting for the browser sign-in")
        finally:
            self.scr.timeout(-1)
        self.busy("Finishing the sign-in…")
        info = flow.finish(res)
        self.msg = "signed in%s" % (" to %s" % info["workspace_name"] if info.get("workspace_name") else "")

    def pick_filter(self, idx=None):
        if idx is None:
            idx = self.popup("Filter", [f["name"] for f in self.cfg["filters"]], self.fidx)
        if idx is not None and idx < len(self.cfg["filters"]):
            self.fidx = idx
            self.query = ""
            self.status_filter = ""
            self.server_status = False
            self.refresh_async("Loading %s…" % self.cfg["filters"][idx]["name"])

    def search(self):
        """Type to narrow the loaded pages at once; Enter also searches the whole database; Esc cancels."""
        buf = self.query
        curses.curs_set(1)
        try:
            while True:
                self.live = buf
                self.refilter()
                self.draw_list()
                h, w = self.scr.getmaxyx()
                self.scr.addnstr(h - 1, 0, ("/ " + buf).ljust(w - 1), w - 1, curses.A_BOLD | curses.A_REVERSE)
                self.scr.move(h - 1, min(2 + len(buf), w - 2))
                k = self.scr.get_wch()
                if k == "\x1b":
                    self.live = ""
                    self.refilter()
                    return
                if k in ("\n", "\r") and self.on_view_tab():
                    self.live = buf.strip()  # keep narrowing the loaded pages; Esc clears
                    self.refilter()
                    self.msg = "narrowing this view by '%s' (Esc clears)" % self.live if self.live else ""
                    return
                if k in ("\n", "\r"):
                    new = buf.strip()
                    self.live = ""
                    changed = new != self.query
                    self.query = new
                    self.refilter()
                    if changed:
                        self.refresh_async("Searching…" if new else "Loading…")
                    return
                if k in ("\x7f", "\b") or k == curses.KEY_BACKSPACE:
                    buf = buf[:-1]
                elif isinstance(k, str) and k.isprintable():
                    buf += k
        finally:
            curses.curs_set(0)

    def pick_status_filter(self):
        """Show only one status: every option of the property, with the number loaded."""
        chips = self.status_chips()
        names = [n for n, _ in chips]
        i = self.popup("Show only", ["(any status)"] + ["%s (%d)" % (n, c) for n, c in chips],
                       1 + names.index(self.status_filter) if self.status_filter in names else 0)
        if i is None:
            return
        self.set_status_filter(names[i - 1] if i else "")

    def pick_view(self):
        """Open one of the database's saved views as a tab (kept until you leave)."""
        self.busy("Reading the views…")
        views = self.client.views()
        if not views:
            self.msg = "this database has no saved views"
            return
        i = self.popup("Notion views", ["%s  (%s)" % (v["name"], v["type"] or "view") for v in views])
        if i is None:
            return
        name = "▦ " + views[i]["name"]
        for n, f in enumerate(self.cfg["filters"]):
            if f.get("view") == views[i]["url"]:
                self.pick_filter(n)
                return
        self.cfg["filters"].append({"name": name, "view": views[i]["url"]})
        self.pick_filter(len(self.cfg["filters"]) - 1)

    # ------------------------------------------------------------ custom tabs and the command palette
    def save_filters(self, filters):
        """Write `filters` as the [[filters]] tables of config.toml. The new text is checked before it replaces the file."""
        path = cfgmod.config_path()
        with open(path, encoding="utf-8") as f:
            text = f.read()
        new = st.replace_filters(text, filters)
        try:
            cfgmod.normalize(tomlcompat.loads(new))
        except ValueError as e:
            raise cfgmod.ConfigError("could not write the tabs: %s" % e)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        self.cfg["filters"] = filters

    def current_view_filter(self):
        """The filter of what is on screen: the tab's own filter, the chosen status and the searched text, ANDed."""
        flt = self.cfg["filters"][self.fidx]
        conds = [flt["filter"]] if flt.get("filter") else []
        if self.status_filter:
            kind = nt.status_options(self.client.discover(), self.cfg["status_property"])[0]
            conds.append({"property": self.cfg["status_property"], kind: {"equals": self.status_filter}})
        text = self.query or self.live
        if text:
            conds.append({"property": self.title_prop or "Name", "title": {"contains": text}})
        return conds[0] if len(conds) == 1 else {"and": conds} if conds else None

    def save_view_as_tab(self):
        """+: keep the current tab, status and search as a new tab."""
        flt = self.cfg["filters"][self.fidx]
        if flt.get("view"):
            self.msg = "this tab is a Notion view: save a normal tab with a status or a search"
            return
        if not (self.status_filter or self.query or self.live):
            self.msg = "choose a status ([ ]) or search (/) first, then press + to keep it"
            return
        suggestion = " · ".join(x for x in (flt["name"], self.status_filter, self.query or self.live) if x)
        name = self.ask("Tab name: ", suggestion)
        if not name or not name.strip():
            return
        new = {"name": name.strip()}
        merged = self.current_view_filter()
        if merged:
            new["filter"] = merged
        if flt.get("sort"):
            new["sort"] = flt["sort"]
        self.save_filters(list(self.cfg["filters"]) + [new])
        self.pick_filter(len(self.cfg["filters"]) - 1)
        self.msg = "saved the tab %s" % new["name"]

    @staticmethod
    def tab_summary(flt):
        if flt.get("view"):
            return "Notion view"
        return nt.clip(json.dumps(flt["filter"], ensure_ascii=False, separators=(",", ":")), 40) if flt.get("filter") else "no filter"

    def manage_tabs(self):
        """T: rename, reorder, delete or add tabs. Every change is written to config.toml."""
        sel = self.fidx
        while True:
            filters = self.cfg["filters"]
            items = ["%s   %s" % (f["name"], self.tab_summary(f)) for f in filters] + ["+ save the current view as a tab"]
            i = self.popup("Tabs", items, min(sel, len(items) - 1))
            if i is None:
                return
            if i == len(filters):
                self.save_view_as_tab()
                return
            sel = i
            act = self.popup(filters[i]["name"], ["Rename", "Move left", "Move right", "Delete"])
            if act is None:
                continue
            current = filters[self.fidx]
            new = list(filters)
            if act == 0:
                name = self.ask("Tab name: ", filters[i]["name"])
                if not name or not name.strip():
                    continue
                new[i] = dict(filters[i], name=name.strip())
                current = new[self.fidx]
            elif act in (1, 2):
                j = i + (-1 if act == 1 else 1)
                if not 0 <= j < len(new):
                    continue
                new[i], new[j] = new[j], new[i]
                sel = j
            else:
                if len(new) == 1:
                    self.msg = "keep at least one tab"
                    continue
                if not self.confirm("Delete the tab %s?" % filters[i]["name"]):
                    continue
                gone = new.pop(i)
                sel = min(i, len(new) - 1)
                if current is gone:
                    current = new[0]
            self.save_filters(new)
            self.fidx = next((n for n, f in enumerate(new) if f is current), 0)
            self.msg = "tabs saved"

    PALETTE = [("Change status", "s"), ("Delegate to an agent", "d"), ("Edit a property", "e"), ("New page", "n"),
               ("Show one status ([ ] moves along them)", "F"), ("Save this view as a tab", "+"), ("Manage tabs", "T"),
               ("Notion views", "V"), ("Sort", "O"), ("Group by status", "g"), ("Compact rows", "z"),
               ("Switch database", "D"), ("Refresh", "r"), ("Open in browser", "o"), ("Copy the link", "y"),
               ("Mouse on or off", "M"), ("Connection and sign-in", "S"), ("Help: every key", "?")]

    def palette(self):
        """:  every command by name, for the ones whose key you do not remember."""
        i = self.popup("Commands", ["%s   %s" % (n, k) for n, k in self.PALETTE])
        if i is not None:
            self.list_key(ord(self.PALETTE[i][1]))

    def clear_filters(self):
        """Esc: drop the status filter and the search before quitting."""
        had_query = bool(self.query) or self.server_status
        self.live, self.status_filter, self.query = "", "", ""
        self.server_status = False
        self.refilter()
        if had_query:
            self.refresh_async("Loading…")
        self.msg = "filters cleared"

    # ------------------------------------------------------------ first-run setup
    def show(self, lines, key=True):
        """Draw a text screen; wait for a key and return it when `key`."""
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        rows = []
        for i, l in enumerate(lines):
            for part in (textwrap.wrap(nt.clean(l) if i else l, max(w - 4, 10), break_long_words=False, break_on_hyphens=False) or [""]):
                rows.append((part, i == 0))
        for i, (part, bold) in enumerate(rows[:h - 2]):
            self.scr.addnstr(1 + i, 2, part, w - 4, curses.A_BOLD if bold else 0)
        self.scr.refresh()
        return self.scr.getch() if key else None

    STEPS = ["Sign in with Notion", "Pick a database", "Your tabs"]
    EXAMPLE = [("○", "Write the launch announcement", "To do", "Ada"), ("◐", "Fix login redirect loop", "In progress", "Grace"),
               ("◐", "Review pricing page copy", "In review", "Linus"), ("✓", "Archive resolved tickets", "Done", "Ada")]

    def page(self, step, lines, key=True):
        """A message screen: the onboarding frame during first-run setup, a plain screen otherwise."""
        return self.onboard(step, lines, key) if self.onboarding else self.show(lines, key)

    def onboard(self, step, lines, key=True, reveal=False):
        """First-run screen: logo, a three-step tracker, a message and a dim preview of the finished list."""
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        bw = max(20, min(w - 6, 46))
        pause = (lambda: (self.scr.refresh(), time.sleep(0.09))) if reveal else (lambda: None)
        self.scr.addnstr(1, 3, "╭" + "─" * (bw - 2) + "╮", w - 4, self.cp(2))
        self.scr.addnstr(2, 3, "│", 1, self.cp(2))
        self.scr.addnstr(2, 5, "◆ herdr-notion", bw - 4, curses.A_BOLD)
        self.scr.addnstr(2, 3 + bw - 1, "│", 1, self.cp(2))
        self.scr.addnstr(3, 3, "│", 1, self.cp(2))
        self.scr.addnstr(3, 5, "Your Notion tasks, inside herdr", bw - 4, curses.A_DIM)
        self.scr.addnstr(3, 3 + bw - 1, "│", 1, self.cp(2))
        self.scr.addnstr(4, 3, "╰" + "─" * (bw - 2) + "╯", w - 4, self.cp(2))
        pause()
        for i, name in enumerate(self.STEPS):
            done, now = i + 1 < step, i + 1 == step
            mark = "✔" if done else "●" if now else "○"
            attr = self.cp(3) if done else (curses.A_BOLD if now else curses.A_DIM)
            self.scr.addnstr(6 + i, 3, "%s  %d  %s" % (mark, i + 1, name), w - 4, attr)
            pause()
        y = 10
        for line in lines:
            for part in (textwrap.wrap(line, max(w - 6, 10)) or [""]):
                if y < h - 2:
                    self.scr.addnstr(y, 3, part, w - 4, 0)
                    y += 1
        if h >= y + 8 and w >= 50:  # a dim example of what the list will look like
            top = max(y + 1, h - 8)
            self.scr.addnstr(top, 3, "example", w - 4, curses.A_DIM)
            self.scr.addnstr(top + 1, 3, " Mine   Current sprint   All", w - 4, curses.A_DIM)
            for i, (m, title, status, who) in enumerate(self.EXAMPLE):
                row = " %s %-32s %-12s %s" % (m, title, status, who)
                self.scr.addnstr(top + 2 + i, 3, row, w - 4, curses.A_DIM)
                pause()
        self.scr.refresh()
        return self.scr.getch() if key else None

    # The Notion cube with its N, drawn in half-blocks from the logo (strokes in the terminal's text colour).
    CUBE_LARGE = [
        '   ▄▄▄▄▄▄▄▄▄▄▄▄▄███████████▄',
        '████▀▀▀▀▀▀▀▀▀▀▀▀▀▀        ▀▀██▄',
        '████▄                        ▀███▄',
        '███████▄▄▄▄▄▄▄▄▄▄▄██████████████████',
        '██████████▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀    ███',
        '███████▀                          ██',
        '███████     ▄▄▄▄▄▄▄     █████▀    ██',
        '███████     ▀██████▄      ██      ██',
        '███████      ████████     ██      ██',
        '███████      ███▀█████▄   ██      ██',
        '███████      ███  ▀█████  ██      ██',
        '███████      ███   ▀█████▄██      ██',
        '███████      ███     ███████      ██',
        '███████      ███      ▀█████      ██',
        ' ▀█████     ▄█████     ▀███▀      ██',
        '   ████                          ███',
        '    ▀███▄▄▄▄▄▄▄▄▄▄▄▄▄███████████████',
        '      ▀████████████▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀',
    ]
    CUBE_TINY = [
        '▄▄██▀▀▀▀▀▀▀▀█▄',
        '██▄▄▄▄▄▄▄▄▄▄▄██▄',
        '███▀       ▄▄  █',
        '███  ▀███  ██  █',
        '███   █▀██▄██  █',
        '███   █  ▀███  █',
        '▀██   ▀▀  ▀▀▀  █',
        '  ▀█▄▄▄▄███████▀',
    ]
    CUBE_SMALL = [
        '▄▄▄▄▄▄▄▄████████▀█▄▄',
        '███                ▀█▄▄',
        '█████▄▄▄▄███████████████',
        '█████▀                ██',
        '█████   ▄▄▄█▄   ▀██▀  ██',
        '█████    █████   ██   ██',
        '█████    ██▀███▄ ██   ██',
        '█████    ██ ▀███▄██   ██',
        '█████    ██   █████   ██',
        '▀████   ▄██▄   ▀██▀   ██',
        '  ▀██            ▄▄▄▄▄██',
        '   ▀████████████▀▀▀▀▀▀▀▀',
    ]

    def draw_splash(self, t):
        """The Notion cube appears row by row, then 'Notion with herdr' and what is loading."""
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        art = (self.CUBE_LARGE if h >= 23 and w >= 40 else self.CUBE_SMALL if h >= 17 and w >= 28
               else self.CUBE_TINY if h >= 12 and w >= 20 else [])
        top = max(0, (h - (len(art) + 4)) // 2) if art else h // 2 - 1
        shown = min(len(art), int(t / 0.045) + 1)
        width = max((len(r) for r in art), default=0)
        for i, row in enumerate(art[:shown]):
            self.scr.addnstr(top + i, max(0, (w - width) // 2), row, w - 1, curses.A_BOLD)
        y = top + len(art) + 1
        if t > 0.95 or not art:
            name = "Notion  with  herdr"
            self.scr.addnstr(y, max(0, (w - len(name)) // 2), name, w - 1, curses.A_BOLD)
        if t > 1.1:
            line = "%s %s" % (self.spin(), self.job["label"] if self.job else "ready")
            self.scr.addnstr(y + 1, max(0, (w - len(line)) // 2), line, w - 1, curses.A_DIM)
        self.scr.refresh()

    def splash(self, minimum=1.7):
        """Show the splash for at least `minimum` seconds and while the first page loads. It cannot be skipped:
        resize events and stray input (a pane gets both right after it starts) must not cut the animation short."""
        if self.scr.getmaxyx()[0] < 5:
            return
        curses.flushinp()
        start = time.time()
        while True:
            self.poll_job()
            t = time.time() - start
            self.draw_splash(t)
            if (not self.job and t >= minimum) or self.reconnect:
                return
            self.scr.timeout(60)
            self.scr.getch()  # only paces the loop; whatever arrives is ignored
            self.scr.timeout(-1)

    def first_run(self):
        """First start: sign in, pick the database, see the tabs. Returns True when config.toml is ready."""
        self.onboarding = True
        try:
            if not oauth.load():
                k = self.onboard(1, ["Browse and update a Notion database from this pane.", "",
                                     "Press Enter to sign in with your browser. Esc quits."], reveal=True)
                if k in (27, ord("q")):
                    return False
                self.guard(self.sign_in)
                if not oauth.load():
                    return False
            if not self.pick_and_save(confirm=False):
                return False
            self.onboard(3, ["Tabs for %s:" % self.picked_title, "  " + "  ·  ".join(self.picked_tabs), "",
                             "The first one opens. S shows the connection; r refreshes.", "", "Press Enter to start."])
            return True
        finally:
            self.onboarding = False

    def pick_database(self):
        """Ask for a database name or Notion link, then which property is the status.
        Returns (choice, schema, status_prop) or None when cancelled or nothing was found."""
        searcher = mb.McpClient("")
        try:
            while True:
                self.page(2, ["Pick the database to browse.", "",
                              "Type part of its name (for example Tasks), or paste its Notion link.",
                              "Esc cancels."], key=False)
                term = self.ask("database: ")
                if not term or not term.strip():
                    return None
                found = searcher.search_databases(term)
                if not found:
                    self.msg = "no database matches %r; try another name or paste its link" % term.strip()
                    self.page(2, ["No database found.", "", self.msg, "", "Press a key to try again."])
                    continue
                if len(found) == 1:
                    i = 0
                else:
                    self.page(2, ["Pick the database to browse."], key=False)
                    i = self.popup("Database", [f["title"] for f in found])
                    if i is None:
                        return None
                choice = found[i] if "id" in found[i] else searcher.database_choice(found[i]["url"])
                break
            schema = mb.McpClient(choice["id"]).discover()
            status_prop = ""
            options = st.status_candidates(schema)
            if len(options) == 1:
                status_prop = options[0]
            elif options:
                self.page(2, ["Pick the status property."], key=False)
                j = self.popup("Which property is the status?", [st.property_label(schema, o) for o in options])
                if j is None:
                    return None
                status_prop = options[j]
            elif not self.confirm("No status property found; s will be disabled. Continue?"):
                return None
        except nt.NotionError as e:
            self.show(["Could not read that database", "", str(e), "", "Press any key."])
            return None
        return choice, schema, status_prop

    def pick_and_save(self, confirm=True):
        picked = self.pick_database()
        if not picked:
            return False
        choice, schema, status_prop = picked
        self.picked_title = choice["title"]
        self.busy("Reading your views and your person page…")
        tabs = self.suggested_tabs(mb.McpClient(choice["id"]), schema, status_prop)
        self.picked_tabs = [f["name"] for f in tabs]
        if confirm and not self.confirm("Replace config.toml for '%s'? Your filters and prompt are regenerated." % choice["title"]):
            return False
        os.makedirs(os.path.dirname(cfgmod.config_path()), exist_ok=True)
        with open(cfgmod.config_path(), "w", encoding="utf-8") as f:
            f.write(st.render_config(choice, schema, status_prop, tabs))
        self.msg = "now browsing %s" % choice["title"]
        return True

    def help(self):
        rows = []
        for title, items in HELP:
            rows.append((title, True))
            rows += [("  %-12s %s" % kv, False) for kv in items]
            rows.append(("", False))
        h, w = self.scr.getmaxyx()
        bw = min(w - 2, max(len(r) for r, _ in rows) + 4)
        bh = min(h - 2, len(rows) + 2)
        win = curses.newwin(bh, bw, (h - bh) // 2, (w - bw) // 2)
        win.box()
        win.addnstr(0, 2, " Keys ", bw - 4, curses.A_BOLD)
        for i, (r, head) in enumerate(rows[:bh - 2]):
            win.addnstr(1 + i, 2, r, bw - 4, curses.A_BOLD if head else 0)
        win.keypad(True)
        win.refresh()
        win.getch()

    def copy(self, what):
        argv = nt.clipboard_cmd(shutil.which)
        if not argv:
            self.msg = "no clipboard tool found (pbcopy, wl-copy, xclip, xsel)"
            return
        if what == "url":
            page = self.current()
            text = page["url"] if page else ""
        else:
            _, text = self.build_prompt()
        if text:
            subprocess.run(argv, input=text.encode(), check=False)
            self.msg = "copied page URL" if what == "url" else "copied delegate prompt"

    def step(self, delta):
        """Open the next/previous page of the list from the detail view."""
        i = self.sel + delta
        if 0 <= i < len(self.pages):
            self.sel = i
            self.open_page(self.pages[i])
        else:
            self.msg = "no more pages"

    # ------------------------------------------------------------ loop
    def run(self):
        curses.curs_set(0)
        self.scr.keypad(True)
        self.init_colors()
        try:
            try:
                self.load_config()
            except cfgmod.NeedsSetup:
                if not self.first_run():
                    return
                self.load_config()
        except (cfgmod.ConfigError, nt.NotionError) as e:
            self.show([str(e), "", "Press any key to quit."])
            return
        signed_in = bool(oauth.load())
        if signed_in:
            self.refresh_async("Loading your pages…")
        self.splash()  # the intro always plays first, loading in the background when there is a login
        if not signed_in and not self.require_sign_in("Sign in to Notion to see your pages."):
            return
        while True:
            self.scr.timeout(-1)  # screens opened from here (connection, review offer) read keys in blocking mode
            if self.want_quit:
                return
            if not oauth.load() and not self.require_sign_in("You are signed out. Sign in to see your pages."):
                return
            if self.reconnect:
                self.connect_screen()
                if self.want_quit:
                    return
            self.poll_job()
            self.track_selection()
            self.maybe_preview()
            self.auto_refresh()
            self.poll_agents()
            if self.finished:
                self.draw_list() if not self.detail else self.draw_detail()
                self.guard(self.offer_review)
            (self.draw_detail if self.detail else self.draw_list)()
            self.scr.refresh()
            self.scr.timeout(100 if self.job else 500 if self.wide() else 1000)
            k = self.scr.getch()
            if k == -1 or k == curses.KEY_RESIZE:
                continue
            self.scr.timeout(-1)  # handlers below (popups, confirm, ask) read keys in blocking mode
            if self.detail:
                if not self.detail_key(k):
                    continue
            elif self.list_key(k) is False:
                return
            if self.want_quit:
                return

    def common_key(self, k):
        if k == ord("s"):
            self.guard(self.change_status)
        elif k == ord("d"):
            self.guard(self.delegate)
        elif k == ord(">"):
            self.guard(self.advance_status)
        elif k == ord("o"):
            page = self.current()
            if page:
                webbrowser.open(page["url"])
                self.msg = "opened in browser"
        elif k == ord("y"):
            self.guard(self.copy, "url")
        elif k == ord("Y"):
            self.guard(self.copy, "prompt")
        elif k == ord("?"):
            self.help()
        elif k == ord("e"):
            self.guard(self.edit_property)
        elif k == ord("S"):
            self.guard(self.settings)

    def list_key(self, k):
        self.msg = "" if k != -1 else self.msg
        n = len(self.pages)
        if k == curses.KEY_MOUSE:
            return self.on_mouse()
        if self.focus == "preview":
            return self.preview_key(k)
        if k == 9 and self.wide() and self.pages:  # Tab: the arrow keys now scroll the preview
            self.focus = "preview"
            return True
        if k in (curses.KEY_LEFT, curses.KEY_RIGHT, ord("h"), ord("l")) and len(self.cfg["filters"]) > 1:
            step = 1 if k in (curses.KEY_RIGHT, ord("l")) else -1
            self.pick_filter((self.fidx + step) % len(self.cfg["filters"]))
            return True
        if k == 27 and self.marked:
            self.marked.clear()
            self.msg = "marks cleared"
            return True
        if k == 27 and (self.query or self.live or self.status_filter):
            self.clear_filters()
            return True
        if k in (ord("q"), 27):
            return False
        if k in (curses.KEY_NPAGE, ord(" "), curses.KEY_PPAGE, ord("b"), ord("J"), ord("K")):
            down = k in (curses.KEY_NPAGE, ord(" "), ord("J"))
            step = 3 if k in (ord("J"), ord("K")) else max(3, self.scr.getmaxyx()[0] - 8)
            if self.wide():  # the preview scrolls
                self.pscroll = max(0, self.pscroll + (step if down else -step))
            else:  # no preview: page through the list
                self.sel = max(0, min(n - 1, self.sel + (step if down else -step)))
            return True
        if k in (curses.KEY_DOWN, ord("j")):
            if self.sel >= n - 1 and self.has_more:
                self.guard(self.load_more)
                n = len(self.pages)
            self.sel = min(self.sel + 1, max(n - 1, 0))
        elif k in (curses.KEY_UP, ord("k")):
            self.sel = max(self.sel - 1, 0)
        elif k in (10, 13, curses.KEY_ENTER) and n:
            self.guard(self.open_page, self.pages[self.sel])
        elif k == ord("f"):
            self.guard(self.pick_filter)
        elif ord("1") <= k <= ord("9"):
            self.guard(self.pick_filter, k - ord("1"))
        elif k == ord("/"):
            self.guard(self.search)
        elif k == ord("F"):
            self.guard(self.pick_status_filter)
        elif k in (ord("["), ord("]")):
            self.cycle_status(1 if k == ord("]") else -1)
        elif k == ord("0") and self.status_filter:
            self.set_status_filter("")
        elif k == ord("V"):
            self.guard(self.pick_view)
        elif k == ord("x"):
            self.toggle_mark()
        elif k == ord("M"):
            self.toggle_mouse()
        elif k == ord("+"):
            self.guard(self.save_view_as_tab)
        elif k == ord("T"):
            self.guard(self.manage_tabs)
        elif k == ord(":"):
            self.guard(self.palette)
        elif k == ord("z"):
            self.compact = not self.compact
            self.msg = "compact: the colored bar shows the status" if self.compact else "table: status word on every row"
        elif k == ord("n"):
            self.guard(self.new_page)
        elif k == ord("D"):
            self.guard(self.switch_database)
        elif k == ord("O"):
            self.sort_mode = (self.sort_mode + 1) % len(self.SORTS)
            self.apply_sort()
            self.msg = "sorted by %s" % self.SORTS[self.sort_mode][1]
        elif k == ord("g"):
            self.group = not self.group
            self.apply_sort()
            self.msg = "grouped by status" if self.group else "ungrouped"
        elif k == ord("r"):
            self.refresh_async("Refreshing…")
        elif k == ord("R"):
            self.guard(self.load_config)
            self.refresh_async("Reloading…")
        else:
            self.common_key(k)
        return True

    def preview_key(self, k):
        """Keys while the preview pane has the focus: scroll it; Tab, Esc or the left arrow hand the focus back."""
        page = max(3, self.scr.getmaxyx()[0] - 8)
        if k in (9, 27, curses.KEY_LEFT, ord("h"), ord("q")):
            self.focus = "list"
        elif k in (curses.KEY_DOWN, ord("j")):
            self.pscroll += 1
        elif k in (curses.KEY_UP, ord("k")):
            self.pscroll = max(0, self.pscroll - 1)
        elif k in (curses.KEY_NPAGE, ord(" "), ord("J")):
            self.pscroll += page
        elif k in (curses.KEY_PPAGE, ord("b"), ord("K")):
            self.pscroll = max(0, self.pscroll - page)
        elif k == ord("g"):
            self.pscroll = 0
        elif k == ord("G"):
            self.pscroll = 10 ** 9
        elif k in (10, 13, curses.KEY_ENTER) and self.pages:
            self.focus = "list"
            self.guard(self.open_page, self.pages[self.sel])
        return True

    def detail_key(self, k):
        h = self.scr.getmaxyx()[0]
        if k == curses.KEY_MOUSE:
            return self.on_mouse()
        if k in (ord("q"), 27, curses.KEY_LEFT, ord("h")):
            self.detail = None
        elif k in (curses.KEY_DOWN, ord("j")):
            self.dscroll += 1
        elif k in (curses.KEY_UP, ord("k")):
            self.dscroll = max(0, self.dscroll - 1)
        elif k in (curses.KEY_NPAGE, ord(" ")):
            self.dscroll += h - 3
        elif k == curses.KEY_PPAGE:
            self.dscroll = max(0, self.dscroll - (h - 3))
        elif k == ord("]"):
            self.guard(self.step, 1)
        elif k == ord("["):
            self.guard(self.step, -1)
        elif k == ord("g"):
            self.dscroll = 0
        elif k == ord("G"):
            self.dscroll = 10 ** 9
        elif k == ord("c"):
            self.guard(self.comment)
        else:
            self.common_key(k)
        return True


def state_file(name):
    d = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.dirname(cfgmod.config_path())
    return os.path.join(d, name)


def log_path():
    d = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.dirname(cfgmod.config_path())
    return os.path.join(d, "error.log")


TITLE = "herdr-notion-tui"  # exact value the open script looks for


def set_title(title):
    """Terminal title: the open script reads it to tell if Notion is already running."""
    try:
        sys.stdout.write("\033]0;%s\007" % title)
        sys.stdout.flush()
    except OSError:
        pass


def main():
    os.environ.setdefault("ESCDELAY", "25")
    set_title(TITLE)
    try:
        curses.wrapper(lambda scr: App(scr).run())
    except KeyboardInterrupt:
        pass
    except Exception as e:  # keep the pane readable instead of dumping a traceback
        path = log_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(traceback.format_exc() + "\n")
            where = "Details: %s" % path
        except OSError:
            where = ""
        set_title("")
        print("herdr-notion crashed: %s: %s\n%s\nPress Enter to close." % (type(e).__name__, e, where))
        try:
            input()
        except EOFError:
            pass
    finally:
        set_title("")
