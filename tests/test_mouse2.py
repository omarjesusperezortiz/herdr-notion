"""Tests for the mouse proposals (popups, footer, cells, headers, marks, board drag, right-click menu).
All with made-up mouse events: whether herdr hands real ones to a plugin pane is not verified."""
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
LEFT, RIGHT = curses.BUTTON1_PRESSED, getattr(curses, "BUTTON3_PRESSED", 0x2000)


def fresh(**kw):
    return make_app(copy.deepcopy(PAGES), **kw)


def event(app, x, y, b=LEFT):
    with mock.patch("curses.getmouse", return_value=(0, x, y, 0, b)):
        return app.send_key(curses.KEY_MOUSE)


def find(app, action, pred=lambda arg: True):
    h = next(h for h in app.hits if h[3] == action and pred(h[4]))
    return h[1], h[0]


class FakeWin:
    made = []

    def __init__(self, h, w, y, x):
        self.geom = (h, w, y, x)
        FakeWin.made.append(self)

    def keypad(self, f): pass
    def erase(self): pass
    def box(self): pass
    def refresh(self): pass
    def addnstr(self, *a): pass
    def addstr(self, *a): pass


def run_popup(app, items, events, **kw):
    """Run app.popup with the (key, mouse tuple or None) events fed to a fake window."""
    FakeWin.made = []
    queue = iter(events)
    cur = {}

    class Win(FakeWin):
        def getch(self):
            k, m = next(queue)
            cur["m"] = m
            return k

    app.read_mouse = lambda: cur.get("m")
    with mock.patch("curses.newwin", lambda *a: Win(*a)):
        return app.popup("Pick", items, **kw), FakeWin.made[-1].geom


class PopupMouse(unittest.TestCase):
    ITEMS = ["One", "Two", "Three", "Four"]

    def geom(self, app):
        # same geometry the popup computes: centered box of len(items) + 2 rows
        h, w = app.scr.getmaxyx()
        _, g = run_popup(app, self.ITEMS, [(27, None)])
        return g

    def test_a_click_on_an_item_picks_it(self):
        app, _ = fresh(h=30, w=100)
        bh, bw, y0, x0 = self.geom(app)
        got, _ = run_popup(app, self.ITEMS, [(curses.KEY_MOUSE, (x0 + 3, y0 + 1 + 2, LEFT))])
        self.assertEqual(got, 2)

    def test_a_click_outside_cancels(self):
        app, _ = fresh(h=30, w=100)
        got, _ = run_popup(app, self.ITEMS, [(curses.KEY_MOUSE, (0, 0, LEFT))])
        self.assertIsNone(got)

    def test_a_click_on_the_border_does_nothing(self):
        app, _ = fresh(h=30, w=100)
        bh, bw, y0, x0 = self.geom(app)
        got, _ = run_popup(app, self.ITEMS, [(curses.KEY_MOUSE, (x0 + 3, y0, LEFT)), (27, None)])
        self.assertIsNone(got)  # the border click was ignored; Esc ended it

    def test_the_wheel_moves_the_selection_and_enter_picks(self):
        app, _ = fresh(h=30, w=100)
        got, _ = run_popup(app, self.ITEMS, [(curses.KEY_MOUSE, (5, 5, app.WHEEL_DOWN)), (curses.KEY_MOUSE, (5, 5, app.WHEEL_DOWN)),
                                             (curses.KEY_MOUSE, (5, 5, app.WHEEL_UP)), (10, None)])
        self.assertEqual(got, 1)

    def test_a_release_event_is_ignored(self):
        app, _ = fresh(h=30, w=100)
        bh, bw, y0, x0 = self.geom(app)
        got, _ = run_popup(app, self.ITEMS, [(curses.KEY_MOUSE, (x0 + 3, y0 + 2, app.RELEASE)), (10, None)])
        self.assertEqual(got, 0)

    def test_at_puts_the_box_at_the_pointer_and_keeps_it_on_screen(self):
        app, _ = fresh(h=30, w=100)
        _, g = run_popup(app, self.ITEMS, [(27, None)], at=(4, 10))
        self.assertEqual((g[2], g[3]), (4, 10))
        _, g = run_popup(app, self.ITEMS, [(27, None)], at=(29, 99))
        self.assertLessEqual(g[2] + g[0], 30)
        self.assertLessEqual(g[3] + g[1], 100)


class HelpMouse(unittest.TestCase):
    def run_help(self, events, h=14):
        app, _ = fresh(h=h, w=100)
        queue = iter(events)
        cur = {}

        class Win(FakeWin):
            def getch(self):
                k, m = next(queue)
                cur["m"] = m
                return k

        app.read_mouse = lambda: cur.get("m")
        FakeWin.made = []
        with mock.patch("curses.newwin", lambda *a: Win(*a)):
            app.help()
        return app

    def test_the_wheel_scrolls_and_a_click_outside_closes(self):
        self.run_help([(curses.KEY_MOUSE, (50, 5, 0x200000)), (curses.KEY_MOUSE, (50, 5, 0x80000)), (curses.KEY_MOUSE, (0, 0, LEFT))])

    def test_a_click_inside_does_not_close(self):
        # the box is centered; (50, 7) is inside it. Esc then ends it.
        self.run_help([(curses.KEY_MOUSE, (50, 7, LEFT)), (27, None)])


class FooterButtons(unittest.TestCase):
    def test_hint_keys(self):
        from herdr_notion.tui import App
        self.assertEqual(App.hint_key("⏎"), 10)
        self.assertEqual(App.hint_key("s"), ord("s"))
        self.assertEqual(App.hint_key("Tab"), 9)
        self.assertEqual(App.hint_key("→"), curses.KEY_RIGHT)
        self.assertIsNone(App.hint_key("↑↓/PgUp PgDn"))
        self.assertIsNone(App.hint_key("h l"))

    def test_fit_hint_parts_match_the_joined_text(self):
        hints = [("⏎", "open"), ("s", "status"), ("d", "delegate")]
        parts = nt.fit_hint_parts(hints, 80)
        self.assertEqual("  ".join(t for _, t in parts), nt.fit_hints(hints, 80))
        self.assertEqual([i for i, _ in parts], [0, 1, 2])

    def test_clicking_a_hint_does_what_its_key_does(self):
        app, _ = fresh(h=28, w=100)
        app.draw_list()
        called = []
        app.change_status = lambda: called.append("status")
        x, y = find(app, "key", lambda k: k == "s")
        event(app, x + 1, y)
        self.assertEqual(called, ["status"])

    def test_a_hint_that_names_several_keys_does_nothing(self):
        app, _ = fresh(h=28, w=150)
        app.draw_list()
        app.focus = "preview"
        app.draw_list()
        x, y = find(app, "key", lambda k: k.startswith("↑↓"))
        event(app, x + 1, y)  # nothing happens, no error
        self.assertEqual(app.focus, "preview")

    def test_the_hint_buttons_are_clickable_on_the_page_view_too(self):
        app, _ = fresh(h=28, w=100)
        app.detail = {"page": app.pages[0], "lines": [], "body": "x"}
        app.dscroll = 0
        app.draw_detail()
        self.assertTrue(any(h[3] == "key" for h in app.hits))

    def test_hints_on_the_board_are_buttons(self):
        app, _ = fresh(h=28, w=110)
        app.list_key(ord("B"))
        app.draw_board()
        x, y = find(app, "key", lambda k: k == "B")
        event(app, x + 1, y)
        self.assertFalse(app.board)


class CellClicks(unittest.TestCase):
    def test_clicking_the_status_cell_opens_the_status_popup_for_that_row(self):
        app, _ = fresh(h=28, w=100)
        app.draw_list()
        got = []
        app.change_status = lambda: got.append(app.pages[app.sel]["id"])
        x, y = find(app, "cell", lambda a: a[1] == "Status" and a[0] == 2)
        event(app, x + 1, y)
        self.assertEqual(got, [app.pages[2]["id"]])

    def test_clicking_the_priority_cell_edits_that_property(self):
        app, _ = fresh(h=28, w=100)
        app.draw_list()
        got = []
        app.edit_property = lambda only=None: got.append((app.pages[app.sel]["id"], only))
        x, y = find(app, "cell", lambda a: a[1] == "Priority" and a[0] == 1)
        event(app, x, y)
        self.assertEqual(got, [(app.pages[1]["id"], "Priority")])

    def test_a_cell_the_database_cannot_edit_says_so(self):
        app, _ = fresh(h=28, w=100)
        app.sel = 0
        app.edit_property(only="Nope")
        self.assertIn("cannot be edited", app.msg)

    def test_the_title_still_selects_and_a_second_click_opens(self):
        app, _ = fresh(h=28, w=100)
        app.draw_list()
        opened = []
        app.open_page = lambda p: opened.append(p["id"])
        event(app, 5, 4)
        event(app, 5, 4)
        self.assertEqual(opened, [app.pages[1]["id"]])


class HeaderSort(unittest.TestCase):
    def test_click_title_sorts_by_title_and_again_reverses(self):
        app, scr = fresh(h=28, w=100)
        app.draw_list()
        x, y = find(app, "sort", lambda a: a == "title")
        event(app, x, y)
        self.assertEqual([nt.page_title(p) for p in app.pages], sorted(nt.page_title(p) for p in app.pages))
        event(app, x, y)
        self.assertEqual([nt.page_title(p) for p in app.pages], sorted((nt.page_title(p) for p in app.pages), reverse=True))
        self.assertTrue(app.sort_rev)

    def test_the_header_shows_an_arrow_for_the_sorted_column(self):
        app, scr = fresh(h=28, w=100)
        app.draw_list()
        self.assertTrue(scr.has("EDITED ▼"))
        app.click_sort("title")
        scr.clear()
        app.draw_list()
        self.assertTrue(scr.has("TITLE ▲"))
        app.click_sort("title")
        scr.clear()
        app.draw_list()
        self.assertTrue(scr.has("TITLE ▼"))

    def test_o_goes_back_to_the_normal_direction(self):
        app, _ = fresh(h=28, w=100)
        app.click_sort("edited")  # the list starts sorted by edit time, so one click reverses it
        self.assertTrue(app.sort_rev)
        app.list_key(ord("O"))
        self.assertFalse(app.sort_rev)

    def test_status_header_sorts_by_status_order(self):
        app, _ = fresh(h=28, w=100)
        app.click_sort("status")
        self.assertEqual([nt.prop_text(p["properties"]["Status"]) for p in app.pages], ["To do", "To do", "In progress", "Review", "Done"])


class MarkClicks(unittest.TestCase):
    def test_clicking_the_bar_marks_and_unmarks_without_moving(self):
        app, _ = fresh(h=28, w=100)
        app.draw_list()
        x, y = find(app, "mark", lambda i: i == 1)
        event(app, 0, y)
        self.assertEqual(app.marked, {app.pages[1]["id"]})
        self.assertEqual(app.sel, 0)
        event(app, 0, y)
        self.assertEqual(app.marked, set())

    def test_a_shift_click_marks_the_range_from_the_last_click(self):
        app, _ = fresh(h=28, w=100)
        app.draw_list()
        _, y1 = find(app, "mark", lambda i: i == 1)
        _, y3 = find(app, "mark", lambda i: i == 3)
        event(app, 0, y1)
        event(app, 0, y3, LEFT | app.MODIFIERS)
        self.assertEqual(app.marked, {app.pages[i]["id"] for i in (1, 2, 3)})

    def test_a_modifier_click_without_an_anchor_marks_just_that_row(self):
        app, _ = fresh(h=28, w=100)
        app.draw_list()
        _, y = find(app, "mark", lambda i: i == 2)
        event(app, 0, y, LEFT | app.MODIFIERS)
        self.assertEqual(app.marked, {app.pages[2]["id"]})


class ContextMenu(unittest.TestCase):
    def test_right_click_selects_the_row_and_opens_the_menu_at_the_pointer(self):
        app, _ = fresh(h=28, w=100)
        app.draw_list()
        seen = {}
        app.popup = lambda title, items, start=0, at=None: seen.update(items=items, at=at) or None
        event(app, 12, 6, RIGHT)
        self.assertEqual(app.sel, 3)
        self.assertEqual(seen["at"], (6, 12))
        self.assertIn("Change status", seen["items"][1])

    def test_choosing_an_item_does_what_its_key_does(self):
        app, _ = fresh(h=28, w=100)
        app.draw_list()
        called = []
        app.change_status = lambda: called.append("status")
        app.popup = lambda title, items, start=0, at=None: 1
        event(app, 12, 6, RIGHT)
        self.assertEqual(called, ["status"])

    def test_right_click_on_empty_space_does_nothing(self):
        app, _ = fresh(h=28, w=100)
        app.draw_list()
        app.popup = lambda *a, **k: self.fail("menu opened")
        event(app, 12, 22, RIGHT)


class BoardDrag(unittest.TestCase):
    def board(self):
        app, scr = fresh(h=28, w=110)
        app.list_key(ord("B"))
        app.draw_board()
        return app, scr

    def card(self, app, ci, r):
        h = next(h for h in app.hits if h[3] == "card" and h[4] == (ci, r))
        return h[1] + 1, h[0]

    def column_x(self, app, ci):
        x0, x1, _ = next(sp for sp in app.board_spans if sp[2] == ci)
        return x0 + 3

    def test_dragging_a_card_onto_another_column_moves_it_after_a_confirm(self):
        app, _ = self.board()
        x, y = self.card(app, 0, 0)
        moved = nt.page_title(app.pages[app.board_columns()[0][1][0]])
        app.confirm = lambda text: True
        event(app, x, y)
        event(app, self.column_x(app, 2), y, app.RELEASE)
        self.assertEqual(len(app.client.writes), 1)
        self.assertEqual(app.client.writes[0][2], "Review")
        self.assertIn(moved, [nt.page_title(app.pages[i]) for n, idx in app.board_columns() if n == "Review" for i in idx])

    def test_declining_the_confirm_writes_nothing(self):
        app, _ = self.board()
        x, y = self.card(app, 0, 0)
        app.confirm = lambda text: False
        event(app, x, y)
        event(app, self.column_x(app, 1), y, app.RELEASE)
        self.assertEqual(app.client.writes, [])

    def test_releasing_on_the_same_column_is_just_a_click(self):
        app, _ = self.board()
        x, y = self.card(app, 0, 0)
        app.confirm = lambda text: self.fail("asked")
        event(app, x, y)
        event(app, x, y, app.RELEASE)
        self.assertEqual(app.client.writes, [])

    def test_a_click_selects_a_card_and_a_second_click_opens_it(self):
        app, _ = self.board()
        opened = []
        app.open_page = lambda p: opened.append(p["id"])
        x, y = self.card(app, 1, 0)
        event(app, x, y)
        self.assertEqual((app.bcol, app.brow), (1, 0))
        event(app, x, y, app.RELEASE)
        event(app, x, y)
        self.assertEqual(len(opened), 1)

    def test_right_click_on_a_card_opens_the_menu(self):
        app, _ = self.board()
        x, y = self.card(app, 0, 1)
        seen = []
        app.popup = lambda title, items, start=0, at=None: seen.append(at) or None
        event(app, x, y, RIGHT)
        self.assertEqual(seen, [(y, x)])
        self.assertEqual((app.bcol, app.brow), (0, 1))

    def test_the_wheel_moves_between_cards(self):
        app, _ = self.board()
        app.bcol = app.brow = 0
        event(app, 5, 8, app.WHEEL_DOWN)
        self.assertEqual(app.brow, 1)

    def test_clicking_a_tab_on_the_board_switches_tab(self):
        app, _ = self.board()
        app.refresh_async = lambda *a, **k: None
        x, y = find(app, "tab", lambda i: i == 1)
        event(app, x, y)
        self.assertEqual(app.fidx, 1)


class MouseMask(unittest.TestCase):
    def test_the_mask_asks_for_releases_right_clicks_and_modifiers(self):
        app, _ = fresh()
        with mock.patch("curses.mousemask") as mm, mock.patch("curses.mouseinterval"):
            app.set_mouse(True)
        mask = mm.call_args[0][0]
        for bit in (curses.BUTTON1_PRESSED, app.RELEASE, app.RIGHT_CLICK, app.WHEEL_UP, app.WHEEL_DOWN, app.MODIFIERS):
            self.assertTrue(mask & bit)
