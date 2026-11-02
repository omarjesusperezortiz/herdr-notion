"""Tests for the UI tasks in TASKS.md, driving the real screens on a fake terminal."""
import copy
import curses
import unittest

from herdr_notion import notion as nt
from tests.support import make_app, page

PAGES = [page(0, "Bravo", "To do", "Low", "2026-10-01T00:00:00Z"), page(1, "Alpha", "Done", "High", "2026-10-03T00:00:00Z"),
         page(2, "Charlie", "In progress", "High", "2026-10-02T00:00:00Z"), page(3, "Delta", "To do", "Low", "2026-10-04T00:00:00Z"),
         page(4, "Echo", "Review", "High", "2026-10-05T00:00:00Z")]


def fresh(**kw):
    return make_app(copy.deepcopy(PAGES), **kw)


def titles(app):
    return [nt.page_title(p) for p in app.pages]


class PreviewCue(unittest.TestCase):
    def long_page(self, w=150, h=14):
        app, scr = fresh(h=h, w=w)
        app.cache[app.pages[0]["id"]] = ["line %02d of the body" % i for i in range(60)]
        return app, scr

    def test_cue_says_how_to_scroll_when_there_is_more_text(self):
        app, scr = self.long_page()
        app.draw_list()
        self.assertTrue(scr.has("Space ▾ more"))

    def test_cue_is_absent_when_the_text_fits(self):
        app, scr = fresh(h=28, w=150)
        app.cache[app.pages[0]["id"]] = ["short"]
        app.draw_list()
        self.assertFalse(scr.has("Space ▾"))

    def test_space_and_b_scroll_the_preview_without_tab_and_keep_the_selection(self):
        app, scr = self.long_page()
        sel = app.sel
        app.list_key(ord(" "))
        app.draw_list()
        self.assertEqual(app.focus, "list")
        self.assertEqual(app.sel, sel)
        self.assertFalse(scr.has("line 00"))
        app.list_key(ord("b"))
        app.draw_list()
        self.assertTrue(scr.has("line 00"))

    def test_cue_has_its_own_line_and_never_covers_text(self):
        app, scr = self.long_page()
        app.draw_list()
        y, _ = scr.find("Space ▾")
        self.assertNotIn("line", scr.row(y)[scr.row(y).index("│"):])
        for n in (0, 7, 100, 10 ** 6):
            app.pscroll = n
            app.draw_list()
            y, _ = scr.find("▾") or scr.find("▴")
            self.assertNotIn("of the body", scr.row(y)[scr.row(y).index("│"):])

    def test_cue_changes_at_the_end_of_the_text(self):
        app, scr = self.long_page()
        app.pscroll = 10 ** 6
        app.draw_list()
        self.assertTrue(scr.has("b ▴ back"))
        self.assertTrue(scr.has("100%"))


class StatusChips(unittest.TestCase):
    def test_chips_follow_the_status_order_with_counts(self):
        app, scr = fresh(h=28, w=100)
        self.assertEqual(app.status_chips(), [("To do", 2), ("In progress", 1), ("Review", 1), ("Done", 1)])
        app.draw_list()
        row = scr.row(1)
        self.assertIn("All 5", row)
        self.assertLess(row.index("To do 2"), row.index("In progress 1"))
        self.assertLess(row.index("Review 1"), row.index("Done 1"))

    def test_brackets_move_along_the_chips_and_zero_clears(self):
        app, scr = fresh(h=28, w=100)
        app.list_key(ord("]"))
        self.assertEqual(app.status_filter, "To do")
        self.assertEqual(sorted(titles(app)), ["Bravo", "Delta"])
        app.list_key(ord("]"))
        self.assertEqual(app.status_filter, "In progress")
        app.list_key(ord("["))
        self.assertEqual(app.status_filter, "To do")
        app.list_key(ord("0"))
        self.assertEqual(app.status_filter, "")
        self.assertEqual(len(titles(app)), 5)

    def test_counts_stay_while_a_chip_is_chosen(self):
        app, scr = fresh(h=28, w=100)
        app.status_filter = "Done"
        app.refilter()
        self.assertEqual(dict(app.status_chips())["To do"], 2)

    def test_typed_text_narrows_the_counts(self):
        app, scr = fresh()
        app.live = "alp"
        self.assertEqual([c for c in app.status_chips() if c[1]], [("Done", 1)])

    def test_every_status_is_listed_even_with_nothing_loaded(self):
        app, scr = fresh(h=28, w=150)
        app.all = [p for p in app.all if nt.prop_text(p["properties"]["Status"]) != "Done"]
        app.refilter()
        self.assertEqual([n for n, _ in app.status_chips()], ["To do", "In progress", "Review", "Done"])
        app.draw_list()
        self.assertIn("Done 0", scr.row(1))

    def test_zero_chips_are_left_out_only_when_the_row_would_not_fit(self):
        app, scr = fresh(h=28, w=75)
        app.all = [p for p in app.all if nt.prop_text(p["properties"]["Status"]) != "Done"]
        app.refilter()
        app.draw_list()
        self.assertNotIn("Done 0", scr.row(1))   # 75 columns: too tight for the four chips, the empty one goes
        self.assertIn("Review 1", scr.row(1))

    def test_a_plus_means_more_pages_exist(self):
        app, scr = fresh(h=28, w=150)
        app.has_more = True
        app.draw_list()
        self.assertIn("All 5+", scr.row(1))
        self.assertIn("To do 2+", scr.row(1))

    def test_narrow_pane_has_no_chip_row(self):
        app, scr = fresh(h=28, w=60)
        app.draw_list()
        self.assertFalse(scr.has("All 5"))
        self.assertTrue(scr.row(1).startswith("─"))

    def test_chip_row_is_clickable_regions(self):
        app, scr = fresh(h=28, w=100)
        app.draw_list()
        names = [h[4] for h in app.hits if h[3] == "status"]
        self.assertEqual(names, ["", "To do", "In progress", "Review", "Done"])


if __name__ == "__main__":
    unittest.main()


class Density(unittest.TestCase):
    def test_table_shows_the_status_word_on_every_row_and_compact_does_not(self):
        app, scr = fresh(h=28, w=100)
        app.draw_list()
        self.assertTrue(scr.has("STATUS"))
        rows = lambda: "\n".join(scr.row(y) for y in range(3, 12))
        self.assertIn("✓ Done", rows())
        app.list_key(ord("z"))
        self.assertTrue(app.compact)
        app.draw_list()
        self.assertFalse(scr.has("STATUS"))
        self.assertNotIn("✓ Done", rows())

    def test_compact_gives_the_title_more_room(self):
        long_title = "A very long ticket title about the signup redirect loop on Safari seventeen"
        pages = [page(0, long_title, "To do", "Low")]
        app, scr = make_app(pages, h=28, w=90)
        app.draw_list()
        before = scr.row(3)
        app.compact = True
        app.draw_list()
        shown = lambda row: row[row.index("A very"):].split("  ")[0]
        self.assertGreater(len(shown(scr.row(3))), len(shown(before)))

    def test_every_row_has_a_bar_and_the_selected_status_is_still_named(self):
        app, scr = fresh(h=28, w=100)
        app.compact = True
        app.draw_list()
        bars = [y for y in range(3, 8) if scr.row(y).startswith("▌")]
        self.assertEqual(len(bars), 5)
        self.assertTrue(scr.has("In progress") or scr.has("Review"))  # the strip under the table names the selected status

    def test_no_color_still_draws_markers(self):
        import os
        os.environ["NO_COLOR"] = "1"
        try:
            app, scr = fresh(h=28, w=100)
            app.init_colors()
            self.assertFalse(app.colors)
            app.draw_list()
            self.assertTrue(scr.has("◐"))
        finally:
            del os.environ["NO_COLOR"]

    def test_the_bar_is_quiet_for_to_do_and_plain_for_done_and_high_priority_is_bold(self):
        import curses
        app, scr = fresh(h=28, w=100)
        app.sel = 0
        app.draw_list()
        by_title = {}
        for y in range(3, 8):
            for t in ("Bravo", "Alpha"):
                if t in scr.row(y):
                    by_title[t] = y
        self.assertTrue(scr.attr_at(by_title["Bravo"], 0) & curses.A_DIM)       # to do
        self.assertFalse(scr.attr_at(by_title["Alpha"], 0) & curses.A_DIM)      # done
        row = scr.row(by_title["Alpha"])
        self.assertTrue(scr.attr_at(by_title["Alpha"], row.index("High")) & curses.A_BOLD)
        row = scr.row(by_title["Bravo"])
        self.assertFalse(scr.attr_at(by_title["Bravo"], row.index("Low")) & curses.A_BOLD)


class CustomTabs(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        self.dir = tempfile.mkdtemp()
        os.environ["HERDR_PLUGIN_CONFIG_DIR"] = self.dir
        with open(os.path.join(self.dir, "config.toml"), "w") as f:
            f.write('[notion]\ndata_source_id = "ds"\nstatus_property = "Status"\n\n'
                    '[[filters]]\nname = "All"\n\n[[filters]]\nname = "Mine"\nfilter = { "property" = "Priority", "select" = { "equals" = "High" } }\n'
                    'sort = { "property" = "Name", "direction" = "ascending" }\n')
        self.app, self.scr = fresh(h=28, w=100)
        from herdr_notion import config as cfgmod
        self.app.cfg = dict(cfgmod.load(), filters=cfgmod.load()["filters"], columns=["Status", "Priority"])
        self.app.cfg["delegate"] = cfgmod.load()["delegate"]
        self.app.refresh_async = lambda label="": None

    def tearDown(self):
        import os
        del os.environ["HERDR_PLUGIN_CONFIG_DIR"]

    def written(self):
        import os
        from herdr_notion import config as cfgmod
        return cfgmod.load(os.path.join(self.dir, "config.toml"))["filters"]

    def test_saving_the_view_combines_tab_status_and_text_and_survives_a_reload(self):
        app = self.app
        app.fidx = 1
        app.status_filter, app.query = "Done", "alp"
        app.ask = lambda label, initial="": "My done alphas"
        app.save_view_as_tab()
        saved = self.written()
        self.assertEqual([f["name"] for f in saved], ["All", "Mine", "My done alphas"])
        flt = saved[2]["filter"]
        self.assertEqual(flt, {"and": [{"property": "Priority", "select": {"equals": "High"}},
                                        {"property": "Status", "status": {"equals": "Done"}},
                                        {"property": "Name", "title": {"contains": "alp"}}]})
        self.assertEqual(saved[2]["sort"], {"property": "Name", "direction": "ascending"})
        self.assertEqual(app.fidx, 2)  # the new tab is selected
        self.assertEqual(saved[1]["sort"], {"property": "Name", "direction": "ascending"})  # hand-written sort kept

    def test_nothing_to_save_without_a_status_or_search(self):
        self.app.save_view_as_tab()
        self.assertIn("choose a status", self.app.msg)
        self.assertEqual([f["name"] for f in self.written()], ["All", "Mine"])

    def test_a_notion_view_tab_cannot_be_saved_again(self):
        self.app.cfg["filters"][0]["view"] = "view://x"
        self.app.status_filter = "Done"
        self.app.save_view_as_tab()
        self.assertIn("Notion view", self.app.msg)

    def test_rename_move_and_delete(self):
        app = self.app
        app.confirm = lambda text: True
        app.ask = lambda label, initial="": "Everything"
        script = iter([0, 0,      # tab All -> Rename
                       0, 2,      # tab Everything -> Move right
                       None])     # close
        app.popup = lambda title, items, start=0: next(script)
        app.manage_tabs()
        self.assertEqual([f["name"] for f in self.written()], ["Mine", "Everything"])
        self.assertEqual(app.cfg["filters"][app.fidx]["name"], "Everything")  # the open tab followed its move
        script = iter([0, 3, None])  # delete Mine
        app.popup = lambda title, items, start=0: next(script)
        app.manage_tabs()
        self.assertEqual([f["name"] for f in self.written()], ["Everything"])
        script = iter([0, 3, None])  # the last tab is kept
        app.popup = lambda title, items, start=0: next(script)
        app.manage_tabs()
        self.assertEqual([f["name"] for f in self.written()], ["Everything"])

    def test_declining_a_delete_changes_nothing(self):
        app = self.app
        app.confirm = lambda text: False
        script = iter([1, 3, None])
        app.popup = lambda title, items, start=0: next(script)
        app.manage_tabs()
        self.assertEqual([f["name"] for f in self.written()], ["All", "Mine"])

    def test_palette_runs_the_chosen_command(self):
        app = self.app
        names = [n for n, _ in app.PALETTE]
        app.popup = lambda title, items, start=0: names.index("Compact rows")
        app.palette()
        self.assertTrue(app.compact)

    def test_colon_opens_the_palette_from_the_list(self):
        app = self.app
        seen = {}
        app.popup = lambda title, items, start=0: seen.setdefault("title", title) and None
        app.list_key(ord(":"))
        self.assertEqual(seen["title"], "Commands")


class BulkAndAdvance(unittest.TestCase):
    def app(self, confirm=True):
        app, scr = fresh(h=28, w=100)
        app.confirm = lambda text: confirm
        return app, scr

    def test_marking_moves_down_and_shows_a_plus(self):
        app, scr = self.app()
        first = app.pages[0]["id"]
        app.list_key(ord("x"))
        self.assertEqual(app.marked, {first})
        self.assertEqual(app.sel, 1)
        app.draw_list()
        self.assertEqual(scr.row(3)[1], "+")
        self.assertIn("1 marked", app.msg)

    def test_s_on_marked_pages_asks_once_and_writes_each(self):
        app, scr = self.app()
        for _ in range(3):
            app.list_key(ord("x"))
        asked = []
        app.confirm = lambda text: asked.append(text) or True
        app.popup = lambda title, items, start=0: (asked.append(title), items.index("Done"))[1]
        app.list_key(ord("s"))
        self.assertEqual(len(app.client.writes), 3)
        self.assertEqual({w[2] for w in app.client.writes}, {"Done"})
        self.assertEqual(len([a for a in asked if "3 pages" in a]), 2)  # popup title and the one confirm
        self.assertEqual(app.marked, set())
        self.assertIn("3 pages", app.toast_state["text"])

    def test_declining_the_bulk_confirm_writes_nothing_and_keeps_the_marks(self):
        app, scr = self.app(confirm=False)
        app.list_key(ord("x"))
        app.list_key(ord("x"))
        app.popup = lambda title, items, start=0: 0
        app.list_key(ord("s"))
        self.assertEqual(app.client.writes, [])
        self.assertEqual(len(app.marked), 2)

    def test_esc_clears_marks_before_anything_else(self):
        app, scr = self.app()
        app.list_key(ord("x"))
        self.assertTrue(app.list_key(27))
        self.assertEqual(app.marked, set())

    def test_advance_moves_to_the_next_option_and_asks_only_the_first_time(self):
        app, scr = self.app()
        confirms = []
        app.confirm = lambda text: confirms.append(text) or True
        app.sel = next(i for i, p in enumerate(app.pages) if nt.page_title(p) == "Bravo")  # To do
        app.list_key(ord(">"))
        self.assertEqual(app.client.writes[-1], ("status", "p0", "In progress"))
        app.list_key(ord(">"))
        self.assertEqual(app.client.writes[-1], ("status", "p0", "Review"))
        self.assertEqual(len(confirms), 1)

    def test_advance_declined_writes_nothing(self):
        app, scr = self.app(confirm=False)
        app.list_key(ord(">"))
        self.assertEqual(app.client.writes, [])
        self.assertEqual(app.msg, "cancelled")

    def test_advance_stops_at_the_last_status(self):
        app, scr = self.app()
        app.sel = next(i for i, p in enumerate(app.pages) if nt.page_title(p) == "Alpha")  # Done
        app.list_key(ord(">"))
        self.assertEqual(app.client.writes, [])
        self.assertIn("last status", app.msg)


class Mouse(unittest.TestCase):
    """Fake mouse events only: whether herdr passes real ones to a plugin pane is not verified."""

    def click(self, app, x, y, button=None):
        import curses
        from unittest import mock
        b = app.WHEEL_UP if button == "up" else app.WHEEL_DOWN if button == "down" else curses.BUTTON1_PRESSED
        with mock.patch("curses.getmouse", return_value=(0, x, y, 0, b)):
            return app.list_key(curses.KEY_MOUSE)

    def test_click_selects_a_row_and_a_second_click_opens_it(self):
        app, scr = fresh(h=28, w=100)
        app.draw_list()
        opened = []
        app.open_page = lambda p: opened.append(p["id"])
        self.click(app, 10, 5)
        self.assertEqual(app.sel, 2)
        self.assertEqual(opened, [])
        self.click(app, 10, 5)
        self.assertEqual(opened, [app.pages[2]["id"]])

    def test_clicking_the_preview_gives_it_the_focus_and_clicking_a_row_takes_it_back(self):
        app, scr = fresh(h=28, w=150)
        app.draw_list()
        self.click(app, 120, 8)
        self.assertEqual(app.focus, "preview")
        self.click(app, 10, 4)
        self.assertEqual(app.focus, "list")
        self.assertEqual(app.sel, 1)

    def test_clicking_empty_list_space_while_in_the_preview_goes_back_to_the_list(self):
        app, scr = fresh(h=28, w=150)
        app.draw_list()
        app.focus = "preview"
        self.click(app, 10, 20)  # below the last row
        self.assertEqual(app.focus, "list")

    def test_a_click_in_the_preview_does_nothing_when_the_pane_has_no_preview(self):
        app, scr = fresh(h=28, w=100)
        app.draw_list()
        self.click(app, 90, 8)
        self.assertEqual(app.focus, "list")

    def test_a_click_in_the_preview_does_nothing_while_the_cursor_is_on_a_group_header(self):
        app, scr = fresh(h=28, w=150)
        app.list_key(ord("g"))
        app.list_key(curses.KEY_UP)
        app.draw_list()
        self.click(app, 120, 8)
        self.assertEqual(app.focus, "list")

    def test_clicking_two_different_rows_does_not_open(self):
        app, scr = fresh(h=28, w=100)
        app.draw_list()
        opened = []
        app.open_page = lambda p: opened.append(p)
        self.click(app, 10, 4)
        self.click(app, 10, 5)
        self.assertEqual(opened, [])

    def test_click_on_a_tab_switches_to_it(self):
        app, scr = fresh(h=28, w=100)
        app.draw_list()
        x = scr.row(0).index("Mine")
        picked = []
        app.pick_filter = lambda i=None: picked.append(i)
        self.click(app, x + 1, 0)
        self.assertEqual(picked, [1])

    def test_click_on_a_status_chip_filters_and_all_clears(self):
        app, scr = fresh(h=28, w=100)
        app.draw_list()
        self.click(app, scr.row(1).index("Done") + 1, 1)
        self.assertEqual(app.status_filter, "Done")
        self.assertEqual(titles(app), ["Alpha"])
        app.draw_list()
        self.click(app, scr.row(1).index("All") + 1, 1)
        self.assertEqual(app.status_filter, "")

    def test_click_on_empty_space_does_nothing(self):
        app, scr = fresh(h=28, w=100)
        app.draw_list()
        sel = app.sel
        self.click(app, 5, 20)
        self.assertEqual(app.sel, sel)

    def test_wheel_scrolls_the_preview_over_it_and_the_list_elsewhere(self):
        app, scr = fresh(h=14, w=150)
        app.cache[app.pages[0]["id"]] = ["line %02d" % i for i in range(60)]
        app.wheel_gap = 0
        app.draw_list()
        self.click(app, 140, 6, "down")
        self.assertEqual(app.pscroll, 2)
        self.assertEqual(app.sel, 0)
        self.click(app, 140, 6, "up")
        self.assertEqual(app.pscroll, 0)
        self.click(app, 10, 6, "down")
        self.assertEqual(app.sel, 1)
        self.assertEqual(app.pscroll, 0)

    def test_m_toggles_the_mouse_and_says_how_to_select_text(self):
        from unittest import mock
        app, scr = fresh()
        with mock.patch("curses.mousemask") as mm, mock.patch("curses.mouseinterval"):
            app.list_key(ord("M"))
            self.assertTrue(app.mouse_on)
            self.assertIn("Option or Shift", app.msg)
            self.assertNotEqual(mm.call_args[0][0], 0)
            app.list_key(ord("M"))
            self.assertFalse(app.mouse_on)
            self.assertEqual(mm.call_args[0][0], 0)

    def test_mouse_is_off_unless_the_config_says_so(self):
        from herdr_notion import config as cfgmod
        base = {"notion": {"data_source_id": "ds"}}
        self.assertFalse(cfgmod.normalize(base)["mouse"])
        self.assertTrue(cfgmod.normalize({"notion": {"data_source_id": "ds", "mouse": True}})["mouse"])


class StatusFromTheServer(unittest.TestCase):
    """With more pages on the server than loaded, a chosen status is part of the query."""

    def more(self):
        app, scr = fresh(h=28, w=150)
        app.has_more = True
        started = []
        app.refresh_async = lambda label="": started.append(label)
        return app, started

    def test_choosing_a_status_asks_notion_when_there_are_more_pages(self):
        app, started = self.more()
        app.set_status_filter("Done")
        self.assertTrue(app.server_status)
        self.assertEqual(started, ["Loading Done…"])
        body = app.build_list_query(app.client.discover())
        self.assertEqual(body["filter"], {"property": "Status", "status": {"equals": "Done"}})

    def test_the_status_is_ANDed_with_the_tab_filter_and_search(self):
        app, started = self.more()
        app.cfg["filters"] = [{"name": "All"}, {"name": "Mine", "filter": {"property": "Priority", "select": {"equals": "High"}}}]
        app.fidx, app.query, app.title_prop = 1, "alp", "Name"
        app.set_status_filter("Done")
        flt = app.build_list_query(app.client.discover())["filter"]
        self.assertEqual(flt["and"][0], {"and": [{"property": "Priority", "select": {"equals": "High"}},
                                                  {"property": "Name", "title": {"contains": "alp"}}]})
        self.assertEqual(flt["and"][1], {"property": "Status", "status": {"equals": "Done"}})

    def test_counts_from_before_stay_on_the_chips_and_clearing_reloads_everything(self):
        app, started = self.more()
        before = app.status_chips()
        app.set_status_filter("Done")
        app.all = [p for p in app.all if nt.prop_text(p["properties"]["Status"]) == "Done"]  # what the server sent back
        app.refilter()
        self.assertEqual(app.status_chips(), before)
        app.set_status_filter("")
        self.assertFalse(app.server_status)
        self.assertEqual(started[-1], "Loading…")
        self.assertNotIn("filter", app.build_list_query(app.client.discover()))

    def test_everything_loaded_means_no_request(self):
        app, scr = fresh(h=28, w=150)
        started = []
        app.refresh_async = lambda label="": started.append(label)
        app.set_status_filter("Done")
        self.assertFalse(app.server_status)
        self.assertEqual(started, [])
        self.assertEqual(titles(app), ["Alpha"])

    def test_a_notion_view_only_narrows_what_is_loaded(self):
        app, started = self.more()
        app.cfg["filters"] = [{"name": "▦ Board", "view": "view://x"}]
        app.fidx = 0
        app.set_status_filter("Done")
        self.assertFalse(app.server_status)
        self.assertEqual(started, [])

    def test_switching_tab_or_pressing_esc_drops_the_server_status(self):
        app, started = self.more()
        app.set_status_filter("Done")
        app.pick_filter(1)
        self.assertFalse(app.server_status)
        self.assertEqual(app.status_filter, "")
        app.set_status_filter("Done")
        app.clear_filters()
        self.assertFalse(app.server_status)

    def test_the_F_picker_offers_every_status(self):
        app, scr = fresh(h=28, w=150)
        offered = {}
        app.popup = lambda title, items, start=0: offered.setdefault("items", items) and None
        app.pick_status_filter()
        self.assertEqual(offered["items"], ["(any status)", "To do (2)", "In progress (1)", "Review (1)", "Done (1)"])


class FoldedGroups(unittest.TestCase):
    def grouped(self):
        app, scr = fresh()
        app.group = True
        app.apply_sort()
        return app, scr

    def test_fold_hides_the_rows_of_the_selected_group_and_keeps_the_selection_visible(self):
        app, _ = self.grouped()
        before = len([1 for k, _ in app.build_display() if k == "r"])
        app.list_key(ord("-"))
        rows = [i for k, i in app.build_display() if k == "r"]
        self.assertLess(len(rows), before)
        self.assertIn(app.sel, rows)
        self.assertEqual(len([1 for k, _ in app.build_display() if k == "h"]), len(set(app.group_of(p) for p in app.pages)))

    def test_arrows_skip_pages_in_a_folded_group(self):
        app, _ = self.grouped()
        app.sel = 0
        app.list_key(ord("-"))
        seen = []
        for _ in range(len(app.pages)):
            seen.append(app.sel)
            app.list_key(ord("j"))
        self.assertFalse(any(app.folded(i) for i in seen))

    def test_equals_folds_all_then_unfolds_all(self):
        app, _ = self.grouped()
        app.list_key(ord("="))
        self.assertEqual([k for k, _ in app.build_display() if k == "r"], [])
        app.list_key(ord("="))
        self.assertTrue(any(k == "r" for k, _ in app.build_display()))

    def test_header_shows_the_arrow_and_a_click_on_it_toggles(self):
        app, scr = self.grouped()
        app.draw_list()
        self.assertTrue(scr.has("▾ "))
        hit = next(h for h in app.hits if h[3] == "group")
        app.toggle_group(hit[4])
        scr.clear() if hasattr(scr, "clear") else None
        app.draw_list()
        self.assertTrue(scr.has("▸ "))

    def test_minus_turns_grouping_on_by_itself(self):
        app, _ = fresh()
        app.list_key(ord("-"))
        self.assertTrue(app.group)
        self.assertEqual(len(app.collapsed), 1)

    def test_g_cycles_list_groups_folded_list(self):
        app, _ = fresh()
        app.list_key(ord("g"))
        self.assertTrue(app.group)
        self.assertEqual(app.collapsed, set())
        app.list_key(ord("g"))
        self.assertTrue(app.group)
        self.assertEqual([k for k, _ in app.build_display() if k == "r"], [])
        app.list_key(ord("g"))
        self.assertFalse(app.group)
        self.assertEqual(app.collapsed, set())


class PreviewSwitch(unittest.TestCase):
    def test_p_hides_and_shows_the_preview_pane(self):
        app, scr = fresh(h=16, w=150)
        app.cache[app.pages[0]["id"]] = ["line %02d" % i for i in range(60)]
        app.draw_list()
        self.assertTrue(app.wide())
        self.assertTrue(scr.has("Space ▾ more"))
        app.list_key(ord("p"))
        self.assertFalse(app.wide())
        scr.clear() if hasattr(scr, "clear") else None
        app.draw_list()
        self.assertFalse(scr.has("Space ▾ more"))
        app.list_key(ord("p"))
        self.assertTrue(app.wide())

    def test_right_arrow_opens_the_page_while_the_preview_is_hidden(self):
        app, _ = fresh(h=16, w=150)
        opened = []
        app.open_page = lambda page: opened.append(page["id"])
        app.list_key(ord("p"))
        app.list_key(curses.KEY_RIGHT)
        self.assertEqual(opened, [app.pages[0]["id"]])
        self.assertEqual(app.focus, "list")

    def test_p_on_a_narrow_pane_explains_instead_of_switching(self):
        app, _ = fresh(h=16, w=90)
        app.list_key(ord("p"))
        self.assertTrue(app.preview_on)
        self.assertIn("too narrow", app.msg)


class HeaderCursor(unittest.TestCase):
    def grouped(self, **kw):
        app, scr = fresh(**kw)
        app.list_key(ord("g"))
        app.sel = 0
        return app, scr

    def top_group(self, app):
        return app.group_of(app.pages[app.sel])

    def test_up_from_the_first_page_of_a_group_lands_on_its_header(self):
        app, _ = self.grouped()
        name = self.top_group(app)
        app.list_key(curses.KEY_UP)
        self.assertEqual(app.hdr, name)
        self.assertIsNone(app.current())

    def test_down_from_a_header_goes_to_the_first_page_of_its_group(self):
        app, _ = self.grouped()
        app.list_key(curses.KEY_UP)
        app.list_key(curses.KEY_DOWN)
        self.assertIsNone(app.hdr)
        self.assertEqual(app.sel, 0)

    def test_left_folds_and_right_unfolds_on_a_header(self):
        app, _ = self.grouped()
        app.list_key(curses.KEY_UP)
        name = app.hdr
        app.list_key(curses.KEY_LEFT)
        self.assertIn(name, app.collapsed)
        self.assertEqual(app.hdr, name)  # the cursor stays on the header
        app.list_key(curses.KEY_RIGHT)
        self.assertNotIn(name, app.collapsed)
        app.list_key(curses.KEY_RIGHT)  # already open: goes into the group
        self.assertIsNone(app.hdr)

    def test_enter_toggles_on_a_header_and_never_opens_a_page(self):
        app, _ = self.grouped()
        app.list_key(curses.KEY_UP)
        app.open_page = lambda page: self.fail("opened a page from a header")
        app.list_key(10)
        self.assertIn(app.hdr, app.collapsed)

    def test_left_from_a_page_goes_up_to_its_group_header(self):
        app, _ = self.grouped()
        name = self.top_group(app)
        app.list_key(curses.KEY_LEFT)
        self.assertEqual(app.hdr, name)

    def test_right_on_a_page_still_goes_into_the_preview(self):
        app, _ = self.grouped(h=16, w=150)
        app.draw_list()
        app.list_key(curses.KEY_RIGHT)
        self.assertEqual(app.focus, "preview")

    def test_actions_do_nothing_while_the_cursor_is_on_a_header(self):
        app, _ = self.grouped()
        app.list_key(curses.KEY_UP)
        app.popup = lambda *a, **k: self.fail("a popup opened")
        app.list_key(ord("s"))
        self.assertEqual(app.client.writes, [])

    def test_the_header_is_drawn_highlighted_with_a_group_summary(self):
        app, scr = self.grouped(h=20, w=150)
        app.list_key(curses.KEY_UP)
        app.draw_list()
        row = next(y for y in range(5) if "▾" in "".join(scr.grid[y]))
        self.assertTrue(scr.attrs[row][3] & curses.A_REVERSE)
        self.assertTrue(scr.has("opens the group"))

    def test_ungrouping_drops_the_header_cursor(self):
        app, _ = self.grouped()
        app.list_key(curses.KEY_UP)
        app.list_key(ord("g"))  # folds
        app.list_key(ord("g"))  # plain list
        app.build_display()
        self.assertIsNone(app.hdr)


class HelpScroll(unittest.TestCase):
    def setUp(self):
        from herdr_notion.tui import App
        self.App = App

    def test_every_key_is_listed_and_long_lines_wrap_instead_of_being_cut(self):
        lines = self.App.help_lines(60)
        text = "\n".join(t for t, _ in lines)
        for key in ("Tab / Shift-Tab", "fold or unfold", "quick filter", "board"):
            self.assertIn(key, text)
        self.assertTrue(all(len(t) <= 60 for t, _ in lines))

    def test_scroll_keys_move_within_bounds_and_other_keys_close(self):
        sc = self.App.help_scroll
        self.assertEqual(sc(curses.KEY_DOWN, 0, 50, 10), 1)
        self.assertEqual(sc(curses.KEY_UP, 0, 50, 10), 0)
        self.assertEqual(sc(ord("G"), 0, 50, 10), 40)
        self.assertEqual(sc(curses.KEY_DOWN, 40, 50, 10), 40)
        self.assertEqual(sc(ord(" "), 0, 50, 10), 9)
        self.assertEqual(sc(curses.KEY_PPAGE, 5, 50, 10), 0)
        self.assertEqual(sc(ord("g"), 30, 50, 10), 0)
        self.assertIsNone(sc(27, 3, 50, 10))
        self.assertIsNone(sc(ord("q"), 3, 50, 10))

    def test_when_everything_fits_any_key_closes(self):
        self.assertIsNone(self.App.help_scroll(curses.KEY_DOWN, 0, 8, 10))

    def test_a_short_pane_gets_a_box_that_scrolls(self):
        app, _ = fresh(h=14, w=100)
        keys = iter([curses.KEY_DOWN, ord("G"), 27])
        seen = []

        class Win:
            def keypad(self, f): pass
            def erase(self): pass
            def box(self): pass
            def refresh(self): pass
            def getch(self): return next(keys)
            def addnstr(self, y, x, text, n, attr=0): seen.append(text)

        saved = curses.newwin
        curses.newwin = lambda *a: Win()
        try:
            app.help()
        finally:
            curses.newwin = saved
        self.assertTrue(any("scroll" in t for t in seen))
        self.assertTrue(any("100%" in t for t in seen))  # reached the end with G
