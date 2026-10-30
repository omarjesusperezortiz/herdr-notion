"""curses TUI: list, detail, popups. All network calls are synchronous."""
import curses
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import traceback
import webbrowser

from . import config as cfgmod
from . import delegate as dg
from . import mcpbackend as mb
from . import oauth
from . import notion as nt
from . import setup as st

HELP = [
    ("Navigate", [("j/k, arrows", "move / scroll"), ("Enter", "open page"),
                  ("] / [", "next / previous page (in a page)"),
                  ("f, 1-9", "switch filter"), ("/", "search by title"), ("Esc, q", "back / quit")]),
    ("Act", [("s", "change status (asks to confirm)"), ("d", "delegate to an agent"),
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
        if not cfg["data_source_id"]:
            raise cfgmod.NeedsSetup("no database picked yet")
        self.client = mb.McpClient(cfg["data_source_id"])
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
        except (nt.NotionError, dg.HerdrError, cfgmod.ConfigError, oauth.OAuthError) as e:
            self.msg = str(e)
            if "Could not find" in self.msg:  # the token cannot see the configured database
                self.msg = "This token cannot see the configured database."
            if self.connection_problem(self.msg):
                self.reconnect = self.msg
        return None

    @staticmethod
    def connection_problem(msg):
        return any(x in msg for x in ("cannot see the configured database", "API token is invalid", "Notion 401",
                                      "not signed in", "login expired", "Notion MCP 401"))

    # ------------------------------------------------------------ data
    def refresh(self):
        self.busy("Loading…")
        schema = self.client.discover()
        if not self.title_prop:
            self.title_prop = nt.title_property_name(schema.get("properties", {}))
        self.groups = nt.status_groups(schema, self.cfg["status_property"])
        flt = self.cfg["filters"][self.fidx]
        body = nt.build_query(flt, self.query, self.title_prop, "me",
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
        kind = nt.group_kind(self.groups.get(text, "")) or nt.guess_kind(text)
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
            self.scr.addstr(y, 2, nt.clip(nt.page_title(p), tw), base | (curses.A_BOLD if on else 0))
            x = 2 + tw
            for n, cw in cols:
                x += 2
                if n == "STATUS":
                    mark, text, attr = self.status_cell(p)
                    self.scr.addstr(y, x, nt.clip("%s %s" % (mark, text), cw), base | attr)
                elif n == "EDITED":
                    self.scr.addnstr(y, x, nt.relative_time(p.get("last_edited_time")), cw, base | curses.A_DIM)
                else:
                    key = next(c for c in extra if c.upper() == n)
                    self.scr.addstr(y, x, nt.clip(nt.prop_text(p["properties"].get(key)), cw), base)
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
        (self.draw_detail if self.detail else self.draw_list)()  # put the screen back behind the popup
        self.scr.refresh()
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

    def connection(self, reason=""):
        """Connection screen: status panel plus a menu. Opened with S, and by itself on a connection problem."""
        sel = 0
        self.reconnect = ""
        self.busy("Checking the connection…")
        status = self.check_connection()
        while True:
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
                if y >= h - len(self.MENU) - 3:
                    break
                if value is None:
                    self.scr.addnstr(y, 2, label, w - 4, curses.A_BOLD)
                else:
                    self.scr.addnstr(y, 2, label.ljust(18), 18, curses.A_BOLD)
                    self.scr.addnstr(y, 20, value, max(w - 22, 1))
                y += 1
            for i, item in enumerate(self.MENU):
                line = "%s %d. %s" % ("❯" if i == sel else " ", i + 1, item)
                self.scr.addnstr(y + i, 2, line, w - 4, curses.A_REVERSE if i == sel else 0)
            self.scr.addnstr(min(y + len(self.MENU) + 1, h - 1), 2, "↑↓ move · Enter choose · 1-%d jump · Esc close" % len(self.MENU), w - 4, curses.A_DIM)
            self.scr.refresh()
            k = self.scr.getch()
            if k in (27, ord("q")):
                if not ok:
                    self.msg = "not connected: press S to fix it"
                return
            if k in (curses.KEY_DOWN, ord("j")):
                sel = min(sel + 1, len(self.MENU) - 1)
                continue
            if k in (curses.KEY_UP, ord("k")):
                sel = max(sel - 1, 0)
                continue
            if ord("1") <= k <= ord("0") + len(self.MENU):
                sel = k - ord("1")
            elif k not in (10, 13, curses.KEY_ENTER):
                continue
            self.msg = ""
            action = (self.sign_in, self.change_database, self.change_status_property, self.reset_filters,
                      self.clear_sign_in, lambda: None)[sel]
            self.guard(action)
            reason = self.msg if self.msg and self.connection_problem(self.msg) else ""
            self.reconnect = ""
            self.busy("Checking the connection…")
            status = self.check_connection()
            if status["signed_in"] and status["db_ok"]:
                reason = ""
                self.guard(self.refresh)
                return

    def reset_filters(self):
        """Replace the filter tabs with the ones this database supports: Mine first, then Current sprint, Open, All."""
        flt = st.suggest_filters(self.client.discover(), self.cfg["status_property"])
        if not self.confirm("Replace the filter tabs with %s? [y/N]" % ", ".join(f["name"] for f in flt)):
            return
        with open(cfgmod.config_path(), encoding="utf-8") as f:
            text = f.read()
        with open(cfgmod.config_path(), "w", encoding="utf-8") as f:
            f.write(st.replace_filters(text, flt))
        self.load_config()
        self.refresh()
        self.msg = "filter tabs: %s" % ", ".join(f["name"] for f in flt)

    def clear_sign_in(self):
        self.msg = "browser sign-in cleared" if oauth.logout() else "was not signed in"

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

    def sign_in(self):
        """Sign in with the browser, inside the pane. The browser reaches a local address on this
        machine; when it is on another machine, copy the link there and paste back the address it ends on."""
        flow = oauth.Flow()
        try:
            webbrowser.open(flow.short)
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
                h, w = self.scr.getmaxyx()
                self.scr.erase()
                rows = [("Sign in to Notion", curses.A_BOLD), ("─" * min(w - 4, 60), curses.A_DIM), ("", 0),
                        ("1  Open the link below (Cmd/Ctrl+click) or press o.", 0), ("", 0), ("      " + flow.short, curses.A_BOLD), ("", 0),
                        ("2  Choose a workspace and approve the connection.", 0),
                        ("3  This screen carries on by itself.", 0), ("", 0),
                        ("Browser on another machine?", curses.A_BOLD),
                        ("   Press c to copy the full link, open it there, approve, then press p", 0),
                        ("   and paste the address the browser ends on (the page will not load).", 0), ("", 0),
                        (note, curses.A_DIM)]
                for y, (line, attr) in enumerate(rows[:h - 3]):
                    self.scr.addnstr(1 + y, 2, line, w - 3, attr)
                    if line.strip() == flow.short:
                        link_at = (1 + y, 8)
                self.scr.addnstr(h - 1, 0, " o open   c copy link   p paste address   Esc cancel".ljust(w - 1), w - 1, curses.A_REVERSE)
                self.scr.refresh()
                try:
                    self.link(link_at[0], link_at[1], flow.short, flow.short)
                except NameError:
                    pass
                k = self.scr.getch()
                if k == 27:
                    flow.close()
                    self.msg = "sign-in cancelled"
                    return
                if k == ord("o"):
                    webbrowser.open(flow.short)
                    note = "opened in the browser"
                elif k == ord("c"):
                    argv = nt.clipboard_cmd(shutil.which)
                    if argv:
                        subprocess.run(argv, input=flow.url.encode(), check=False)
                        note = "full link copied"
                    else:
                        note = "no clipboard tool found; use the link above"
                elif k == ord("p"):
                    self.scr.timeout(-1)
                    pasted = self.ask("address: ")
                    self.scr.timeout(300)
                    if pasted and pasted.strip():
                        res = flow.parse(pasted)
                        break
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
        rows = []
        for i, l in enumerate(lines):
            for part in (textwrap.wrap(nt.clean(l) if i else l, max(w - 4, 10), break_long_words=False, break_on_hyphens=False) or [""]):
                rows.append((part, i == 0))
        for i, (part, bold) in enumerate(rows[:h - 2]):
            self.scr.addnstr(1 + i, 2, part, w - 4, curses.A_BOLD if bold else 0)
        self.scr.refresh()
        return self.scr.getch() if key else None

    def first_run(self):
        """First start: sign in, then pick the database to browse. Returns True when config.toml is ready."""
        if not oauth.load():
            self.show(["Welcome to herdr-notion", "",
                       "Browse and update a Notion database from this pane.",
                       "First, sign in with your Notion account.", "", "Press a key to sign in. Esc quits."])
            self.guard(self.sign_in)
            if not oauth.load():
                return False
        return self.pick_and_save(confirm=False)

    def pick_database(self):
        """Ask for a database name or Notion link, then which property is the status.
        Returns (choice, schema, status_prop) or None when cancelled or nothing was found."""
        searcher = mb.McpClient("")
        try:
            while True:
                self.show(["Pick the database to browse", "",
                           "Type part of its name (for example Tasks), or paste its Notion link.",
                           "Esc cancels."], key=False)
                term = self.ask("database: ")
                if not term or not term.strip():
                    return None
                found = searcher.search_databases(term)
                if not found:
                    self.msg = "no database matches %r; try another name or paste its link" % term.strip()
                    self.show(["No database found", "", self.msg, "", "Press a key to try again."])
                    continue
                if len(found) == 1:
                    i = 0
                else:
                    self.show(["Pick the database to browse"], key=False)
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
                self.show(["Pick the status property"], key=False)
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
        if confirm and not self.confirm("Replace config.toml for '%s'? Your filters and prompt are regenerated. [y/N]" % choice["title"]):
            return False
        os.makedirs(os.path.dirname(cfgmod.config_path()), exist_ok=True)
        with open(cfgmod.config_path(), "w", encoding="utf-8") as f:
            f.write(st.render_config(choice, schema, status_prop))
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
        self.guard(self.refresh)
        while True:
            if self.reconnect:
                self.connect_screen()
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
        elif k == ord("S"):
            self.guard(self.settings)

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
