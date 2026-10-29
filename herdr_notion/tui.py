"""curses TUI: list, detail, popups. All network calls are synchronous."""
import curses
import os
import shutil
import subprocess
import webbrowser

from . import config as cfgmod
from . import delegate as dg
from . import notion as nt
from . import setup as st

HELP = [
    ("Navigate", [("j/k, arrows", "move / scroll"), ("Enter", "open page"),
                  ("] / [", "next / previous page (in a page)"),
                  ("f, 1-9", "switch filter"), ("/", "search by title"), ("Esc, q", "back / quit")]),
    ("Act", [("s", "change status (asks to confirm)"), ("d", "delegate to an agent"),
             ("o", "open in browser"), ("y", "copy page URL"), ("Y", "copy delegate prompt")]),
    ("Config", [("r", "refresh"), ("R", "reload config")]),
]

class App:
    def __init__(self, scr):
        self.scr = scr
        self.msg = ""
        self.cfg = None
        self.client = None
        self.fidx = 0
        self.query = ""
        self.pages = []
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
        token = cfgmod.resolve_token(cfg)
        self.client = nt.Client(token, cfg["database_id"], cfg["data_source_id"])
        self.title_prop = cfg["title_property"]
        self.groups = {}
        self.fidx = 0
        self.cfg = cfg

    def busy(self, text):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        self.scr.addnstr(h // 2, 2, text, w - 4)
        self.scr.refresh()

    def guard(self, fn, *a):
        """Run fn, turning expected errors into a status message."""
        try:
            return fn(*a)
        except (nt.NotionError, dg.HerdrError, cfgmod.ConfigError) as e:
            self.msg = str(e)
        return None

    # ------------------------------------------------------------ data
    def refresh(self):
        self.busy("Loading…")
        schema = self.client.discover()
        if not self.title_prop:
            self.title_prop = nt.title_property_name(schema.get("properties", {}))
        self.groups = nt.status_groups(schema, self.cfg["status_property"])
        flt = self.cfg["filters"][self.fidx]
        body = nt.build_query(flt, self.query, self.title_prop, self.cfg["me_user_id"],
                              self.cfg["page_size"])
        self.pages = self.client.query(body).get("results", [])
        self.sel = self.top = 0
        self.msg = ""

    def open_page(self, page):
        self.busy("Loading page…")
        page = self.client.get_page(page["id"])
        if page["id"] not in self.cache:
            self.cache[page["id"]] = self.client.body_lines(page["id"])
        body = self.cache[page["id"]]
        lines = [nt.page_title(page), ""]
        lines += ["%s: %s" % kv for kv in nt.page_properties(page)]
        lines += ["", page.get("url", ""), ""] + body
        self.detail = {"page": page, "lines": lines, "body": "\n".join(body)}
        self.dscroll = 0

    def current(self):
        if self.detail:
            return self.detail["page"]
        return self.pages[self.sel] if self.pages else None

    # ------------------------------------------------------------ drawing
    MARK = {"todo": "○", "progress": "◐", "done": "✓", "": "●"}
    PAIR = {"todo": 1, "progress": 2, "done": 3, "": 0}

    def init_colors(self):
        self.colors = False
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_YELLOW, -1)
            curses.init_pair(2, curses.COLOR_CYAN, -1)
            curses.init_pair(3, curses.COLOR_GREEN, -1)
            curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_CYAN)  # active tab
            self.colors = True
        except curses.error:
            pass

    def cp(self, n):
        return curses.color_pair(n) if self.colors else 0

    def status_cell(self, page):
        """(marker, text, color attr) for the status property of a page."""
        prop = self.cfg["status_property"]
        text = nt.prop_text(page["properties"].get(prop))
        kind = nt.group_kind(self.groups.get(text, ""))
        return self.MARK[kind], text, self.cp(self.PAIR[kind])

    def rule(self, y, w):
        self.scr.addnstr(y, 0, "─" * (w - 1), w - 1, curses.A_DIM)

    def draw_tabs(self, w):
        x = 1
        self.scr.addnstr(0, 0, " Notion ", w - 1, curses.A_BOLD)
        x = 9
        for i, f in enumerate(self.cfg["filters"]):
            label = " %d %s " % (i + 1, f["name"])
            if x + len(label) >= w:
                break
            self.scr.addnstr(0, x, label, w - 1 - x,
                             self.cp(4) | curses.A_BOLD if i == self.fidx and self.colors
                             else curses.A_REVERSE if i == self.fidx else curses.A_DIM)
            x += len(label) + 1
        right = "%d pages%s " % (len(self.pages), ' · / "%s"' % self.query if self.query else "")
        if x + len(right) < w:
            self.scr.addnstr(0, w - 1 - len(right), right, len(right), curses.A_DIM)

    def draw_list(self):
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        self.draw_tabs(w)
        self.rule(1, w)
        # Columns right of the title, in priority order: status, other columns, edited.
        # They are dropped from the right until the title has at least 20 columns.
        extra = [c for c in self.cfg["columns"] if c != self.cfg["status_property"]]
        cols = [("STATUS", 14)] + [(c.upper(), min(14, max(len(c), 6))) for c in extra] + [("EDITED", 6)]
        while cols and w - 4 - sum(cw + 2 for _, cw in cols) < 20:
            cols.pop()
        tw = max(10, w - 4 - sum(cw + 2 for _, cw in cols))
        head = "  " + "TITLE".ljust(tw) + "".join("  " + n.ljust(cw) for n, cw in cols)
        self.scr.addnstr(2, 0, head, w - 1, curses.A_DIM | curses.A_BOLD)
        rows = h - 6
        if self.sel < self.top:
            self.top = self.sel
        if self.sel >= self.top + rows:
            self.top = self.sel - rows + 1
        for r, p in enumerate(self.pages[self.top:self.top + rows]):
            y, on = 3 + r, self.top + r == self.sel
            base = curses.A_REVERSE if on else 0
            self.scr.addnstr(y, 0, " " * (w - 1), w - 1, base)
            self.scr.addnstr(y, 0, "▌" if on else " ", 1, base | self.cp(2))
            self.scr.addnstr(y, 2, nt.page_title(p)[:tw], tw, base | (curses.A_BOLD if on else 0))
            x = 2 + tw
            for n, cw in cols:
                x += 2
                if n == "STATUS":
                    mark, text, attr = self.status_cell(p)
                    self.scr.addnstr(y, x, ("%s %s" % (mark, text))[:cw], cw, base | attr)
                elif n == "EDITED":
                    self.scr.addnstr(y, x, nt.relative_time(p.get("last_edited_time")), cw, base | curses.A_DIM)
                else:
                    key = next(c for c in extra if c.upper() == n)
                    self.scr.addnstr(y, x, nt.prop_text(p["properties"].get(key))[:cw], cw, base)
                x += cw
        if not self.pages:
            self.scr.addnstr(4, 2, "No pages match this filter.", w - 3, curses.A_DIM)
        self.rule(h - 3, w)
        self.footer([("⏎", "open"), ("s", "status"), ("d", "delegate"), ("f", "filters"), ("/", "search"), ("?", "more")])

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
        view = h - 5
        self.dscroll = max(0, min(self.dscroll, max(0, len(lines) - view)))
        for r, (l, attr) in enumerate(lines[self.dscroll:self.dscroll + view]):
            self.scr.addnstr(2 + r, 0, l, w - 1, attr)
        self.rule(h - 3, w)
        if len(lines) > view:
            ind = "%d%%" % min(100, 100 * (self.dscroll + view) // len(lines))
            self.scr.addnstr(h - 3, max(0, w - len(ind) - 2), ind, len(ind), curses.A_DIM)
        self.footer([("s", "status"), ("d", "delegate"), ("o", "browser"), ("q", "back"), ("]", "next"), ("y", "copy"), ("?", "more")])

    def footer(self, hints):
        h, w = self.scr.getmaxyx()
        ctx = self.cfg["filters"][self.fidx]["name"] if self.cfg else ""
        # keep the filter name only when the hints still have room
        text = nt.fit_hints(hints, w - 2 - (len(ctx) + 3 if ctx else 0))
        if len(text) < len(nt.fit_hints(hints, w - 2)):
            ctx = ""
            text = nt.fit_hints(hints, w - 2)
        if self.msg:
            self.scr.addnstr(h - 2, 1, self.msg, w - 2, curses.A_BOLD)
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
            bw = min(w - 2, max([len(title)] + [len(i) + 5 for i in items]) + 2)
            bh = min(h - 2, len(items) + 2)
            y0, x0 = (h - bh) // 2, (w - bw) // 2
            win = curses.newwin(bh, bw, y0, x0)
            win.box()
            win.addnstr(0, 2, " %s " % title, bw - 4, curses.A_BOLD)
            first = max(0, min(sel - (bh - 3), len(items) - (bh - 2)))
            for r, it in enumerate(items[first:first + bh - 2]):
                i = first + r
                tag = "%d " % (i + 1) if i < 9 else "  "
                win.addnstr(1 + r, 1, (tag + it).ljust(bw - 2), bw - 2,
                            curses.A_REVERSE if i == sel else 0)
            win.refresh()
            k = self.scr.getch()
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
            self.scr.touchwin()

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

    def confirm(self, text):
        h, w = self.scr.getmaxyx()
        self.scr.addnstr(h - 1, 0, (text + " [y/N]").ljust(w - 1), w - 1, curses.A_BOLD)
        return self.scr.getch() in (ord("y"), ord("Y"))

    # ------------------------------------------------------------ actions
    def change_status(self):
        page = self.current()
        if not page:
            return
        prop = self.cfg["status_property"]
        kind, options = nt.status_options(self.client.discover(), prop)
        i = self.popup("Set %s" % prop, options)
        if i is None:
            return
        if not self.confirm("Set %s to '%s'?" % (prop, options[i])):
            self.msg = "cancelled"
            return
        self.busy("Updating…")
        updated = self.client.set_status(page["id"], prop, kind, options[i])
        for n, p in enumerate(self.pages):
            if p["id"] == updated["id"]:
                self.pages[n] = updated
        if self.detail:
            self.open_page(updated)
        self.msg = "%s set to %s · press d to delegate it" % (prop, options[i])

    def build_prompt(self):
        """(page, rendered prompt) for the current page, fetching the body if needed."""
        page = self.current()
        if not page:
            return None, None
        if self.detail:
            body = self.detail["body"]
        else:
            self.busy("Loading page…")
            page = self.client.get_page(page["id"])
            if page["id"] not in self.cache:
                self.cache[page["id"]] = self.client.body_lines(page["id"])
            body = "\n".join(self.cache[page["id"]])
        dcfg = self.cfg["delegate"]
        return page, nt.render_prompt(dcfg["prompt"], page, body, dcfg["max_body_chars"])

    def delegate(self):
        page, prompt = self.build_prompt()
        if not page:
            return
        dcfg = self.cfg["delegate"]
        agents = dg.list_agents()
        items = [dg.agent_label(a) for a in agents] + ["+ start new agent…"]
        i = self.popup("Delegate to", items)
        if i is None:
            return
        if i < len(agents):
            self.busy("Sending prompt…")
            dg.send_prompt(agents[i]["pane_id"], prompt)
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
        self.busy("Starting %s…" % kinds[j]["name"])
        pane = dg.start_agent(kinds[j]["command"], cwd, nt.page_title(page)[:30], dcfg,
                              os.environ.get("HERDR_WORKSPACE_ID"))
        dg.send_prompt(pane, prompt)
        self.msg = "started %s and sent prompt" % kinds[j]["name"]

    def pick_filter(self, idx=None):
        if idx is None:
            idx = self.popup("Filter", [f["name"] for f in self.cfg["filters"]], self.fidx)
        if idx is not None and idx < len(self.cfg["filters"]):
            self.fidx = idx
            self.query = ""
            self.refresh()

    def search(self):
        q = self.ask("search: ", self.query)
        if q is not None:
            self.query = q.strip()
            self.refresh()

    # ------------------------------------------------------------ first-run setup
    def show(self, lines, key=True):
        """Draw a text screen; wait for a key and return it when `key`."""
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        for i, l in enumerate(lines[:h - 2]):
            bold = i == 0
            self.scr.addnstr(1 + i, 2, l, w - 4, curses.A_BOLD if bold else 0)
        self.scr.refresh()
        return self.scr.getch() if key else None

    def wizard(self):
        """Connect a token and pick a database. Writes config.toml and the token
        file. Returns True when the config is ready."""
        self.show(["Welcome to herdr-notion", "",
                   "1. Open https://www.notion.so/profile/integrations",
                   "2. New integration, type Internal, save, and copy the secret.",
                   "", "Press a key, then paste the secret (it is hidden). Esc quits."])
        client = None
        while client is None:
            self.scr.erase()
            self.show(["Paste the integration secret below", "", self.msg], key=False)
            token = self.ask("secret: ", secret=True)
            if token is None:
                return False
            token = token.strip()
            try:
                me = nt.Client(token, "").request("GET", "/users/me")
                client = nt.Client(token, "")
                self.msg = ""
            except nt.NotionError as e:
                self.msg = str(e)
        name = me.get("name") or "your integration"
        try:
            while True:
                choices = st.database_choices(client.request("POST", "/search", st.SEARCH_BODY))
                if choices:
                    break
                k = self.show(["Connected as %s" % name, "",
                               "No database is shared with it yet. In Notion:",
                               "  open your database, click ••• (top right) → Connections,",
                               "  and add %s." % name, "",
                               "Press r to check again, q to quit."])
                if k in (ord("q"), 27):
                    return False
            self.show(["Connected as %s" % name, "", "Pick the database to browse."], key=False)
            i = self.popup("Database", [c["title"] for c in choices])
            if i is None:
                return False
            choice = choices[i]
            schema = client.request("GET", "/data_sources/" + choice["id"])
            status_prop = ""
            options = st.status_candidates(schema)
            if len(options) == 1:
                status_prop = options[0]
            elif options:
                j = self.popup("Status property (changed with s)", options)
                if j is None:
                    return False
                status_prop = options[j]
            elif not self.confirm("No status property found; s will be disabled. Continue?"):
                return False
        except nt.NotionError as e:
            self.show(["Setup failed", "", str(e), "", "Press any key."])
            return False
        os.makedirs(os.path.dirname(cfgmod.config_path()), exist_ok=True)
        with open(cfgmod.config_path(), "w", encoding="utf-8") as f:
            f.write(st.render_config(choice, schema, status_prop))
        cfgmod.save_token(token)
        self.msg = "Connected to %s" % choice["title"]
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
        win.refresh()
        self.scr.getch()

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
                if not self.wizard():
                    return
                self.load_config()
        except (cfgmod.ConfigError, nt.NotionError) as e:
            self.show([str(e), "", "Press any key to quit."])
            return
        self.guard(self.refresh)
        while True:
            (self.draw_detail if self.detail else self.draw_list)()
            self.scr.refresh()
            k = self.scr.getch()
            if k == curses.KEY_RESIZE:
                continue
            if self.detail:
                if not self.detail_key(k):
                    continue
            elif self.list_key(k) is False:
                return

    def common_key(self, k):
        if k == ord("s"):
            self.guard(self.change_status)
        elif k == ord("d"):
            self.guard(self.delegate)
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

    def list_key(self, k):
        self.msg = "" if k != -1 else self.msg
        n = len(self.pages)
        if k in (ord("q"), 27):
            return False
        if k in (curses.KEY_DOWN, ord("j")):
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
        elif k == ord("r"):
            self.guard(self.refresh)
        elif k == ord("R"):
            self.guard(self.load_config)
            self.guard(self.refresh)
        else:
            self.common_key(k)
        return True

    def detail_key(self, k):
        h = self.scr.getmaxyx()[0]
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
        else:
            self.common_key(k)
        return True


def main():
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(lambda scr: App(scr).run())
