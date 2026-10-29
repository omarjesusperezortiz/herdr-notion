"""curses TUI: list, detail, popups. All network calls are synchronous."""
import curses
import os
import webbrowser

from . import config as cfgmod
from . import delegate as dg
from . import notion as nt

HELP = [
    "j/k, arrows   move / scroll",
    "Enter         open page",
    "f, 1-9        switch filter",
    "/             search by title",
    "s             change status",
    "d             delegate to an agent",
    "o             open in browser",
    "r / R         refresh / reload config",
    "Esc, q        back / quit",
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

    # ------------------------------------------------------------ setup
    def load_config(self):
        self.cfg = cfgmod.load()
        token = cfgmod.read_token(self.cfg["api_token_cmd"])
        self.client = nt.Client(token, self.cfg["database_id"], self.cfg["data_source_id"])
        self.title_prop = self.cfg["title_property"]

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
    def draw_list(self):
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        flt = self.cfg["filters"][self.fidx]["name"]
        head = "Notion: %s%s (%d)" % (flt, ' / "%s"' % self.query if self.query else "", len(self.pages))
        self.scr.addnstr(0, 0, head, w, curses.A_BOLD)
        cols = self.cfg["columns"]
        widths = [min(16, max([len(c)] + [len(nt.prop_text(p["properties"].get(c))) for p in self.pages]))
                  for c in cols]
        tw = max(10, w - 2 - sum(x + 2 for x in widths))
        rows = h - 3
        if self.sel < self.top:
            self.top = self.sel
        if self.sel >= self.top + rows:
            self.top = self.sel - rows + 1
        for r, p in enumerate(self.pages[self.top:self.top + rows]):
            cells = [nt.page_title(p)[:tw].ljust(tw)]
            cells += [nt.prop_text(p["properties"].get(c))[:wd].ljust(wd) for c, wd in zip(cols, widths)]
            attr = curses.A_REVERSE if self.top + r == self.sel else 0
            self.scr.addnstr(1 + r, 0, ("  ".join(cells)).ljust(w - 1), w - 1, attr)
        self.footer("Enter open · f filters · / search · s status · d delegate · o browser · ? help")

    def draw_detail(self):
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        lines = []
        for l in self.detail["lines"]:
            lines += [l[i:i + w - 1] for i in range(0, max(len(l), 1), max(w - 1, 1))]
        self.dscroll = max(0, min(self.dscroll, max(0, len(lines) - (h - 2))))
        for r, l in enumerate(lines[self.dscroll:self.dscroll + h - 2]):
            self.scr.addnstr(r, 0, l, w - 1, curses.A_BOLD if self.dscroll + r == 0 else 0)
        self.footer("j/k scroll · s status · d delegate · o browser · q back")
        self._dlen = len(lines)

    def footer(self, text):
        h, w = self.scr.getmaxyx()
        self.scr.addnstr(h - 2, 0, text, w - 1, curses.A_DIM)
        self.scr.addnstr(h - 1, 0, self.msg, w - 1, curses.A_BOLD)

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

    def ask(self, label, initial=""):
        """One-line text input; Esc cancels (returns None)."""
        buf = initial
        curses.curs_set(1)
        try:
            while True:
                h, w = self.scr.getmaxyx()
                self.scr.addnstr(h - 1, 0, (label + buf).ljust(w - 1), w - 1)
                self.scr.move(h - 1, min(len(label) + len(buf), w - 1))
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
        self.msg = "%s set to %s" % (prop, options[i])

    def delegate(self):
        page = self.current()
        if not page:
            return
        if not self.detail:
            self.open_page(page)
            page = self.detail["page"]
            self.detail = None
            body = "\n".join(self.cache[page["id"]])
        else:
            body = self.detail["body"]
        dcfg = self.cfg["delegate"]
        prompt = nt.render_prompt(dcfg["prompt"], page, body, dcfg["max_body_chars"])
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

    def help(self):
        self.popup("Keys", HELP)

    # ------------------------------------------------------------ loop
    def run(self):
        curses.curs_set(0)
        self.scr.keypad(True)
        if self.guard(self.load_config) is None and not self.cfg:
            self.scr.erase()
            self.scr.addnstr(1, 2, self.msg, self.scr.getmaxyx()[1] - 4)
            self.scr.addnstr(3, 2, "Press any key to quit.", 30)
            self.scr.getch()
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
