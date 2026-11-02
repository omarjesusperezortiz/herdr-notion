"""Filter pills (A1), toasts and undo (A2) and the other polish tasks in TASKS-UX.md."""
import copy
import curses
import time
import unittest
from unittest import mock

from herdr_notion import notion as nt
from tests.support import make_app, page

PAGES = [page(0, "Bravo", "To do", "Low", "2026-10-01T00:00:00Z"), page(1, "Alpha", "Done", "High", "2026-10-03T00:00:00Z"),
         page(2, "Charlie", "In progress", "High", "2026-10-02T00:00:00Z"), page(3, "Delta", "To do", "Low", "2026-10-04T00:00:00Z"),
         page(4, "Echo", "Review", "High", "2026-10-05T00:00:00Z")]


def fresh(**kw):
    return make_app(copy.deepcopy(PAGES), **kw)


class Pills(unittest.TestCase):
    def test_no_filter_no_pill_row(self):
        app, scr = fresh(h=28, w=100)
        self.assertEqual(app.filter_pills(), [])
        app.draw_list()
        self.assertFalse(scr.has("Esc removes the last"))
        self.assertTrue(scr.row(2).strip().startswith("TITLE"))

    def test_status_quick_and_search_become_pills_in_the_order_esc_removes_them(self):
        app, scr = fresh(h=28, w=110)
        app.set_status_filter("Review")
        app.quick = "week"
        app.live = "ech"
        app.refilter()
        self.assertEqual([k for k, _ in app.filter_pills()], ["status", "quick", "search"])
        app.draw_list()
        row = scr.row(2)
        for text in ("Review ×", "changed this week ×", "“ech” ×", "Esc removes the last"):
            self.assertIn(text, row)
        self.assertTrue(scr.row(3).strip().startswith("TITLE"))  # the table moved down one row

    def test_esc_removes_one_filter_at_a_time_newest_first_and_then_quits(self):
        app, _ = fresh(h=28, w=110)
        app.set_status_filter("Review")
        app.quick = "week"
        app.refilter()
        app.list_key(27)
        self.assertEqual(app.quick, "")
        self.assertEqual(app.status_filter, "Review")
        app.list_key(27)
        self.assertEqual(app.status_filter, "")
        self.assertIs(app.list_key(27), False)  # nothing left to remove: Esc quits

    def test_a_pill_click_removes_just_that_filter(self):
        app, scr = fresh(h=28, w=110)
        app.set_status_filter("Review")
        app.quick = "today"
        app.refilter()
        app.draw_list()
        x, y = next((h[1] + 1, h[0]) for h in app.hits if h[3] == "pill" and h[4] == "status")
        with mock.patch("curses.getmouse", return_value=(0, x, y, 0, curses.BUTTON1_PRESSED)):
            app.list_key(curses.KEY_MOUSE)
        self.assertEqual(app.status_filter, "")
        self.assertEqual(app.quick, "today")

    def test_rows_and_hits_follow_the_pill_row(self):
        app, scr = fresh(h=28, w=110)
        app.quick = "week"
        app.refilter()
        app.all[0]["last_edited_time"] = "2999-01-01T00:00:00Z"
        app.refilter()
        app.draw_list()
        rows = [h for h in app.hits if h[3] == "row"]
        if rows:
            self.assertEqual(min(h[0] for h in rows), 4)


class Toasts(unittest.TestCase):
    def test_a_status_change_toasts_with_undo_and_the_toast_is_drawn_in_the_footer(self):
        app, scr = fresh(h=28, w=100)
        app.popup = lambda title, items, start=0, at=None: items.index("Review")
        app.confirm = lambda text: True
        app.sel = 0
        app.change_status()
        self.assertEqual(app.toast_state["kind"], "ok")
        self.assertIn("u undo", app.toast_state["text"])
        app.draw_list()
        self.assertTrue(scr.has("✓ Status set to Review · u undo"))

    def test_a_toast_expires(self):
        app, scr = fresh(h=28, w=100)
        app.toast("hello")
        self.assertIsNotNone(app.live_toast())
        with mock.patch("time.time", return_value=time.time() + 60):
            self.assertIsNone(app.live_toast())

    def test_an_error_toast_is_red_and_stays_until_a_key(self):
        app, scr = fresh(h=28, w=100)
        app.colors = True
        app.pairs = {("red", False): 3, ("green", False): 2}
        app.report(nt.NotionError("Notion 429, try again"))
        self.assertEqual(app.toast_state["kind"], "err")
        with mock.patch("curses.color_pair", lambda n: n << 8):
            app.draw_list()
        row = next(y for y in range(24, 28) if "429" in "".join(scr.grid[y]))
        col = "".join(scr.grid[row]).index("✕")
        self.assertEqual(scr.attrs[row][col] >> 8 & 0xff, 3)
        # it outlives the 8 s of an ok toast
        with mock.patch("time.time", return_value=time.time() + 10):
            self.assertIsNotNone(app.live_toast())

    def test_the_run_loop_drops_an_error_toast_at_the_next_key(self):
        from herdr_notion import tui
        app, _ = fresh()
        app.toast("boom", "err")
        # the loop clears an err toast after any key but a mouse event: same condition as in run()
        self.assertTrue(app.toast_state and app.toast_state["kind"] == "err")

    def test_a_job_label_wins_over_a_toast_while_loading(self):
        app, scr = fresh(h=28, w=100)
        app.toast("older message")
        app.job = {"kind": "list", "label": "Loading…", "done": False, "seq": 1, "on_done": None}
        app.draw_list()
        self.assertTrue(scr.has("Loading…"))
        self.assertFalse(scr.has("older message"))


class Undo(unittest.TestCase):
    def status(self, app, i):
        return nt.prop_text(app.pages[i]["properties"]["Status"])

    def test_u_puts_the_old_status_back(self):
        app, _ = fresh()
        app.popup = lambda title, items, start=0, at=None: items.index("Review")
        app.confirm = lambda text: True
        app.sel = 0
        old = self.status(app, 0)
        app.change_status()
        self.assertEqual(self.status(app, 0), "Review")
        app.list_key(ord("u"))
        self.assertEqual(self.status(app, 0), old)
        self.assertIn("undone", app.toast_state["text"])
        self.assertEqual(len(app.client.writes), 2)

    def test_undo_covers_a_bulk_change(self):
        app, _ = fresh()
        app.marked = {app.pages[0]["id"], app.pages[1]["id"]}
        before = {p["id"]: nt.prop_text(p["properties"]["Status"]) for p in app.pages}
        app.popup = lambda title, items, start=0, at=None: items.index("Review")
        app.confirm = lambda text: True
        app.change_status()
        app.undo_last()
        after = {p["id"]: nt.prop_text(p["properties"]["Status"]) for p in app.pages}
        self.assertEqual(before, after)

    def test_undo_after_advance_and_board_move(self):
        app, _ = fresh()
        app.sel = 0
        app.advance_ok = True
        old = self.status(app, 0)
        app.advance_status()
        self.assertNotEqual(self.status(app, 0), old)
        app.undo_last()
        self.assertEqual(self.status(app, 0), old)

    def test_nothing_to_undo_says_so_and_writes_nothing(self):
        app, _ = fresh()
        app.undo_last()
        self.assertEqual(app.toast_state["text"], "nothing to undo")
        self.assertEqual(app.client.writes, [])

    def test_undo_expires_after_a_minute_and_works_only_once(self):
        app, _ = fresh()
        app.popup = lambda title, items, start=0, at=None: items.index("Review")
        app.confirm = lambda text: True
        app.change_status()
        app.undo_last()
        n = len(app.client.writes)
        app.undo_last()
        self.assertEqual(len(app.client.writes), n)
        app.change_status()
        with mock.patch("time.time", return_value=time.time() + 120):
            app.undo_last()
        self.assertEqual(len(app.client.writes), n + 1)  # only the second change; the expired undo wrote nothing

    def test_u_is_listed_in_the_help(self):
        from herdr_notion.tui import HELP
        self.assertTrue(any(k == "u" for _, items in HELP for k, _ in items))
