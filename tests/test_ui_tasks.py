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
