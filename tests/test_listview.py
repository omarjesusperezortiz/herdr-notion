import unittest
from unittest import mock

from herdr_notion import notion as nt
from tests.support import make_app, page

PAGES = [page(0, "Bravo", "To do", "Low", "2026-10-01T00:00:00Z"), page(1, "Alpha", "Done", "High", "2026-10-03T00:00:00Z"),
         page(2, "Charlie", "In progress", "High", "2026-10-02T00:00:00Z"), page(3, "Delta", "To do", "Low", "2026-10-04T00:00:00Z"),
         page(4, "Révision du menú", "Review", "High", "2026-10-05T00:00:00Z")]


def titles(app):
    return [nt.page_title(p) for p in app.pages]


class Drawing(unittest.TestCase):
    def test_narrow_pane_shows_the_table_and_a_strip_with_the_selected_page(self):
        app, scr = make_app([dict(p) for p in PAGES], h=28, w=90)
        app.draw_list()
        self.assertTrue(scr.has("Notion"))
        self.assertTrue(scr.has("TITLE"))
        self.assertTrue(scr.has("◐ In progress"))
        self.assertTrue(scr.has("✓ Done"))
        self.assertTrue(scr.has("edited"))  # the strip under the table

    def test_wide_pane_shows_a_preview_of_the_selected_page(self):
        app, scr = make_app([dict(p) for p in PAGES], h=28, w=150)
        app.cache[app.pages[0]["id"]] = ["## Goal", "Protect the search endpoint from bursts."]
        app.draw_list()
        self.assertTrue(scr.has("Protect the search endpoint"))

    def test_preview_scrolls_and_starts_over_for_another_page(self):
        app, scr = make_app([dict(p) for p in PAGES], h=14, w=150)
        app.cache[app.pages[0]["id"]] = ["line %02d of the body" % i for i in range(60)]
        app.draw_list()
        self.assertTrue(scr.has("line 00"))
        self.assertFalse(scr.has("line 40"))
        for _ in range(8):
            app.list_key(ord("J"))
        app.draw_list()
        self.assertFalse(scr.has("line 00"))
        self.assertTrue(scr.has("line 2"))
        app.list_key(ord("K"))
        app.draw_list()
        self.assertTrue(scr.has("line 21") or scr.has("line 20"))
        app.sel = 1
        app.track_selection()
        self.assertEqual(app.pscroll, 0)

    def test_scroll_keys_page_the_list_when_there_is_no_preview(self):
        many = [page(i, "Task %02d" % i) for i in range(40)]
        app, scr = make_app(many, h=20, w=90)
        app.list_key(curses_key("NPAGE"))
        self.assertGreater(app.sel, 3)
        app.list_key(ord("b"))
        self.assertLess(app.sel, 3)


def curses_key(name):
    import curses
    return getattr(curses, "KEY_" + name)


class Filtering(unittest.TestCase):
    def test_typing_narrows_the_loaded_pages_without_accents_or_case(self):
        app, scr = make_app([dict(p) for p in PAGES])
        app.scr.keys = list("revision")
        with mock.patch("curses.curs_set", lambda n: None):
            app.scr.keys.append("\x1b")  # Esc ends the search after we look
            real = app.draw_list
            seen = []

            def spy():
                real()
                seen.append(titles(app))
            app.draw_list = spy
            app.search()
        self.assertIn(["Révision du menú"], seen)      # narrowed while typing
        self.assertEqual(len(titles(app)), 5)          # Esc restored everything

    def test_enter_searches_the_whole_database(self):
        app, scr = make_app([dict(p) for p in PAGES])
        scr.keys = list("alp") + ["\n"]
        with mock.patch("curses.curs_set", lambda n: None):
            app.search()
        self.assertEqual(app.query, "alp")
        self.assertEqual(app.live, "")
        app.job["on_done"] and None
        self.assertIsNotNone(app.job)  # a server search was started

    def test_esc_clears_the_search_and_status_filter_before_quitting(self):
        app, scr = make_app([dict(p) for p in PAGES])
        app.status_filter = "To do"
        app.refilter()
        self.assertEqual(sorted(titles(app)), ["Bravo", "Delta"])
        self.assertTrue(app.list_key(27))              # clears, does not quit
        self.assertEqual(app.status_filter, "")
        self.assertEqual(len(titles(app)), 5)
        self.assertFalse(app.list_key(27))             # nothing left to clear: quit

    def test_status_filter_lists_the_statuses_that_exist(self):
        app, scr = make_app([dict(p) for p in PAGES])
        offered = {}

        def popup(title, items, start=0):
            offered["items"] = items
            return items.index("Done (1)")
        app.popup = popup
        app.pick_status_filter()
        self.assertEqual(offered["items"][0], "(any status)")
        self.assertEqual(titles(app), ["Alpha"])
        app.draw_list()
        self.assertTrue(scr.has("status: Done"))

    def test_selection_stays_on_the_same_page_when_filtering(self):
        app, scr = make_app([dict(p) for p in PAGES])
        app.sel = next(i for i, p in enumerate(app.pages) if nt.page_title(p) == "Delta")
        app.status_filter = "To do"
        app.refilter()
        self.assertEqual(nt.page_title(app.pages[app.sel]), "Delta")


class Ordering(unittest.TestCase):
    def test_newest_edit_first_title_and_status_orders(self):
        app, _ = make_app([dict(p) for p in PAGES])
        self.assertEqual(titles(app)[0], "Révision du menú")
        app.sort_mode = 1
        app.apply_sort()
        self.assertEqual(titles(app), ["Alpha", "Bravo", "Charlie", "Delta", "Révision du menú"])
        app.sort_mode = 2  # the order of the options in Notion: To do, In progress, Review, Done
        app.apply_sort()
        self.assertEqual(titles(app)[:2], ["Bravo", "Delta"])
        self.assertEqual(titles(app)[-1], "Alpha")

    def test_groups_have_headers_with_counts(self):
        app, scr = make_app([dict(p) for p in PAGES], h=28, w=90)
        app.group = True
        app.apply_sort()
        app.draw_list()
        self.assertTrue(scr.has("To do (2)"))
        self.assertTrue(scr.has("Done (1)"))

    def test_selection_follows_the_page_through_a_sort(self):
        app, _ = make_app([dict(p) for p in PAGES])
        app.sel = next(i for i, p in enumerate(app.pages) if nt.page_title(p) == "Charlie")
        app.sort_mode = 1
        app.apply_sort()
        self.assertEqual(nt.page_title(app.pages[app.sel]), "Charlie")


class Unread(unittest.TestCase):
    def test_only_edits_after_the_last_look_are_unread(self):
        app, scr = make_app([dict(p) for p in PAGES])
        self.assertFalse(any(app.unread(p) for p in app.pages))
        edited = dict(app.pages[0], last_edited_time="2099-01-01T00:00:00Z")
        self.assertTrue(app.unread(edited))
        app.mark_seen(edited)
        self.assertFalse(app.unread(edited))

    def test_the_dot_is_drawn_next_to_an_unread_title(self):
        app, scr = make_app([dict(p) for p in PAGES], h=28, w=90)
        app.seen[app.pages[1]["id"]] = "2000-01-01T00:00:00Z"
        app.draw_list()
        self.assertTrue(any(scr.row(y).lstrip("▌ ").startswith("•") or scr.row(y)[1:2] == "•" for y in range(3, 10)))


class Paging(unittest.TestCase):
    def test_moving_past_the_last_row_loads_the_next_page(self):
        many = [page(i, "Task %02d" % i, edited="2026-10-%02dT00:00:00Z" % (30 - i // 2)) for i in range(25)]
        app, _ = make_app(many, size=10)
        self.assertEqual(len(app.pages), 10)
        self.assertTrue(app.has_more)
        app.sel = 9
        app.list_key(ord("j"))
        self.assertIsNotNone(app.job)
        app.job["thread_done"] = None
        import time
        for _ in range(50):
            app.poll_job()
            if app.job is None:
                break
            time.sleep(0.05)
        self.assertEqual(len(app.pages), 20)


if __name__ == "__main__":
    unittest.main()


class Views(unittest.TestCase):
    def view_app(self, pages, size=None):
        app, scr = make_app(pages, size=size)
        app.cfg["filters"] = [{"name": "All"}, {"name": "▦ Board", "view": "https://notion.test/db?v=1"}]
        app.fidx = 1
        app.client.view_calls = []
        return app, scr

    def test_a_view_tab_asks_for_the_view_and_keeps_its_order(self):
        app, _ = self.view_app([dict(p) for p in PAGES])
        body = app.build_list_query({})
        self.assertEqual(body["view_url"], "https://notion.test/db?v=1")
        app.all = [dict(p) for p in PAGES]
        app.apply_sort()
        self.assertEqual(titles(app), ["Bravo", "Alpha", "Charlie", "Delta", "Révision du menú"])  # as given, not newest first
        app.sort_mode = 1
        app.apply_sort()
        self.assertEqual(titles(app)[0], "Alpha")  # choosing a sort still works

    def test_search_on_a_view_narrows_locally(self):
        app, scr = self.view_app([dict(p) for p in PAGES])
        app.all = [dict(p) for p in PAGES]
        app.refilter()
        scr.keys = list("char") + ["\n"]
        with mock.patch("curses.curs_set", lambda n: None):
            app.search()
        self.assertEqual(titles(app), ["Charlie"])
        self.assertIsNone(app.job)  # no server search for a view
        self.assertTrue(app.list_key(27))
        self.assertEqual(len(titles(app)), 5)

    def test_picking_a_view_adds_a_tab_once(self):
        app, _ = make_app([dict(p) for p in PAGES])
        app.client.views = lambda: [{"name": "Board", "type": "board", "url": "https://notion.test/db?v=9"}]
        app.popup = lambda *a, **k: 0
        app.pick_filter = lambda idx: setattr(app, "fidx", idx)
        app.pick_view()
        self.assertEqual(app.cfg["filters"][-1], {"name": "▦ Board", "view": "https://notion.test/db?v=9"})
        n = len(app.cfg["filters"])
        app.pick_view()
        self.assertEqual(len(app.cfg["filters"]), n)


class GroupOrder(unittest.TestCase):
    def headers(self, app):
        return [text for kind, text in app.build_display() if kind == "h"]

    def test_groups_follow_the_order_of_the_status_options_in_notion(self):
        app, _ = make_app([dict(p) for p in PAGES])
        app.group = True
        app.apply_sort()
        self.assertEqual([h.split(" (")[0][2:] for h in self.headers(app)], ["To do", "In progress", "Review", "Done"])

    def test_group_order_in_the_config_wins(self):
        app, _ = make_app([dict(p) for p in PAGES])
        app.cfg["group_order"] = ["Review", "In progress"]
        app.set_status_order(app.client.discover())
        app.group = True
        app.apply_sort()
        self.assertEqual([h.split(" (")[0][2:] for h in self.headers(app)], ["Review", "In progress", "To do", "Done"])


class Focus(unittest.TestCase):
    def wide_app(self):
        app, scr = make_app([dict(p) for p in PAGES], h=16, w=150)
        app.cache[app.pages[0]["id"]] = ["line %02d" % i for i in range(60)]
        return app, scr

    def test_right_arrow_gives_the_arrow_keys_to_the_preview(self):
        app, scr = self.wide_app()
        app.draw_list()
        self.assertTrue(scr.has("Space ▾ more"))
        app.list_key(curses_key("RIGHT"))
        self.assertEqual(app.focus, "preview")
        sel = app.sel
        for _ in range(5):
            app.list_key(curses_key("DOWN"))
        self.assertEqual(app.sel, sel)          # the list did not move
        self.assertEqual(app.pscroll, 5)        # the preview did
        app.draw_list()
        self.assertFalse(scr.has("line 00"))
        self.assertTrue(scr.has("← back"))
        app.list_key(curses_key("NPAGE"))
        self.assertGreater(app.pscroll, 5)
        app.list_key(curses_key("LEFT"))
        self.assertEqual(app.focus, "list")

    def test_esc_returns_to_the_list_not_out_of_the_app(self):
        app, _ = self.wide_app()
        app.list_key(curses_key("RIGHT"))
        self.assertTrue(app.list_key(27))
        self.assertEqual(app.focus, "list")

    def test_right_arrow_opens_the_page_when_there_is_no_preview(self):
        app, _ = make_app([dict(p) for p in PAGES], h=20, w=90)
        opened = []
        app.open_page = lambda p: opened.append(p["id"])
        app.list_key(curses_key("RIGHT"))
        self.assertEqual(app.focus, "list")
        self.assertEqual(opened, [app.pages[0]["id"]])

    def test_left_arrow_on_the_list_does_nothing(self):
        app, _ = self.wide_app()
        sel, fidx = app.sel, app.fidx
        self.assertTrue(app.list_key(curses_key("LEFT")))
        self.assertEqual((app.sel, app.fidx, app.focus), (sel, fidx, "list"))


class Tabs(unittest.TestCase):
    def test_tab_and_shift_tab_cycle_the_tabs(self):
        app, _ = make_app([dict(p) for p in PAGES])
        app.cfg["filters"] = [{"name": n} for n in ("A", "B", "C")]
        seen = []
        app.pick_filter = lambda idx=None: seen.append(idx)
        app.list_key(9)
        app.list_key(curses_key("BTAB"))
        self.assertEqual(seen, [1, 2])  # next from 0 is 1; previous from 0 wraps to 2
