"""Tests for the UI tasks in TASKS.md, driving the real screens on a fake terminal."""
import unittest

from herdr_notion import notion as nt
from tests.support import make_app, page

PAGES = [page(0, "Bravo", "To do", "Low", "2026-10-01T00:00:00Z"), page(1, "Alpha", "Done", "High", "2026-10-03T00:00:00Z"),
         page(2, "Charlie", "In progress", "High", "2026-10-02T00:00:00Z"), page(3, "Delta", "To do", "Low", "2026-10-04T00:00:00Z"),
         page(4, "Echo", "Review", "High", "2026-10-05T00:00:00Z")]


def fresh(**kw):
    return make_app([dict(p) for p in PAGES], **kw)


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
