"""A stand-in for the curses screen, so the real UI code can be drawn and driven in tests without a terminal."""
import os
import tempfile
from unittest import mock


class FakeScreen:
    def __init__(self, h=30, w=100):
        self.h, self.w = h, w
        self.keys = []
        self.erase()

    # drawing
    def erase(self):
        self.grid = [[" "] * self.w for _ in range(self.h)]

    clear = erase

    def addnstr(self, y, x, text, n, attr=0):
        for i, ch in enumerate(str(text)[:max(n, 0)]):
            if 0 <= y < self.h and 0 <= x + i < self.w:
                self.grid[y][x + i] = ch

    def addstr(self, y, x, text, attr=0):
        self.addnstr(y, x, text, len(str(text)), attr)

    # no-ops
    def refresh(self): pass
    def move(self, y, x): pass
    def keypad(self, flag): pass
    def timeout(self, ms): pass
    def getmaxyx(self): return self.h, self.w

    # input: `keys` holds ints (special keys, ord) or one-character strings for get_wch
    def getch(self):
        k = self.keys.pop(0) if self.keys else -1
        return ord(k) if isinstance(k, str) else k

    def get_wch(self):
        if not self.keys:
            raise AssertionError("the code asked for a key the test did not provide")
        return self.keys.pop(0)

    # reading back
    def row(self, y):
        return "".join(self.grid[y]).rstrip()

    def text(self):
        return "\n".join(self.row(y) for y in range(self.h))

    def has(self, needle):
        return needle in self.text()


def page(i, title, status="To do", priority="Low", edited="2026-10-30T10:00:00.000Z"):
    return {"id": "p%d" % i, "url": "https://example.test/p%d" % i, "last_edited_time": edited, "properties": {
        "Name": {"type": "title", "title": [{"plain_text": title}]},
        "Status": {"type": "status", "status": {"name": status}},
        "Priority": {"type": "select", "select": {"name": priority}}}}


SCHEMA = {"title": [{"plain_text": "Demo"}], "properties": {
    "Name": {"type": "title", "title": {}},
    "Status": {"type": "status", "status": {"options": [{"id": n, "name": n} for n in ("To do", "In progress", "Review", "Done")],
                                            "groups": [{"name": "To-do", "option_ids": ["To do"]},
                                                       {"name": "In progress", "option_ids": ["In progress", "Review"]},
                                                       {"name": "Complete", "option_ids": ["Done"]}]}},
    "Priority": {"type": "select", "select": {"options": [{"id": "High", "name": "High"}, {"id": "Low", "name": "Low"}]}}}}


class FakeClient:
    """Stands in for McpClient: serves `pages` (in pages of `size` when has_more is wanted) and records writes."""

    def __init__(self, pages, size=None, body=None):
        self.pages, self.size, self.writes = pages, size, []
        self.body = body or ["## Goal", "Body of the page."]
        self.queries = []

    def discover(self): return SCHEMA
    def whoami(self): return {"workspace": "Demo", "user": "Ada"}
    def body_lines(self, pid): return list(self.body)
    def get_page(self, pid): return next(p for p in self.pages if p["id"] == pid)

    def query(self, body, cursor=None, seen=()):
        self.queries.append((body, cursor))
        start = int(cursor["at"]) if cursor else 0
        size = self.size or len(self.pages)
        chunk = self.pages[start:start + size]
        more = start + size < len(self.pages)
        return {"results": chunk, "has_more": more, "cursor": {"at": start + size} if more else None, "note": ""}

    def set_status(self, pid, prop, kind, option):
        self.writes.append(("status", pid, option))
        page_ = self.get_page(pid)
        page_["properties"]["Status"]["status"]["name"] = option
        return page_


CONFIG = {"status_property": "Status", "columns": ["Status", "Priority"], "filters": [{"name": "All"}, {"name": "Mine"}],
          "page_size": 50, "title_property": "", "data_source_id": "ds", "database_id": "",
          "delegate": {"prompt": "{title}", "max_body_chars": 1000, "default_cwd": "", "startup_delay_ms": 0,
                       "wait_ready_ms": 0, "agents": [{"name": "claude", "command": ["claude"]}]}}


def make_app(pages, h=30, w=100, size=None, state_dir=None):
    """A real App drawing on a FakeScreen with a FakeClient behind it."""
    from herdr_notion.tui import App
    os.environ["HERDR_PLUGIN_STATE_DIR"] = state_dir or tempfile.mkdtemp()
    scr = FakeScreen(h, w)
    with mock.patch("curses.curs_set", lambda n: None):
        app = App(scr)
    app.cfg = dict(CONFIG)
    app.client = FakeClient(pages, size)
    app.title_prop = "Name"
    app.groups = {"To do": "To-do", "In progress": "In progress", "Review": "In progress", "Done": "Complete"}
    app.apply_list(SCHEMA, {}, app.client.query({}))
    return app, scr
