"""Tests for the UI tasks in TASKS.md, driving the real screens on a fake terminal."""
import copy
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
        self.assertEqual(app.status_chips(), [("Done", 1)])

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
        self.assertIn("3 pages", app.msg)

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
