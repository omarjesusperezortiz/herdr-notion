import unittest

from herdr_notion.tui import App


def page(i, title, status, edited):
    return {"id": "p%d" % i, "last_edited_time": edited, "properties": {
        "Name": {"type": "title", "title": [{"plain_text": title}]},
        "Status": {"type": "status", "status": {"name": status}}}}


def make(pages):
    a = App.__new__(App)  # no curses screen needed for ordering logic
    a.cfg = {"status_property": "Status"}
    a.groups = {"To do": "To-do", "Doing": "In progress", "Done": "Complete"}
    a.pages, a.sel, a.sort_mode, a.group = pages, 0, 0, False
    a.seen, a.save_seen = {}, lambda: None
    return a


PAGES = [page(0, "Bravo", "To do", "2026-10-01T00:00:00Z"), page(1, "Alpha", "Done", "2026-10-03T00:00:00Z"),
         page(2, "Charlie", "Doing", "2026-10-02T00:00:00Z"), page(3, "Delta", "To do", "2026-10-04T00:00:00Z")]


class Sorting(unittest.TestCase):
    def titles(self, a):
        return [p["properties"]["Name"]["title"][0]["plain_text"] for p in a.pages]

    def test_newest_edit_first_by_default(self):
        a = make(list(PAGES))
        a.apply_sort()
        self.assertEqual(self.titles(a), ["Delta", "Alpha", "Charlie", "Bravo"])

    def test_title_and_status_orders(self):
        a = make(list(PAGES))
        a.sort_mode = 1
        a.apply_sort()
        self.assertEqual(self.titles(a), ["Alpha", "Bravo", "Charlie", "Delta"])
        a.sort_mode = 2  # in progress, then to do, then done
        a.apply_sort()
        self.assertEqual(self.titles(a), ["Charlie", "Bravo", "Delta", "Alpha"])

    def test_selection_follows_the_page(self):
        a = make(list(PAGES))
        a.sel = 2  # Charlie
        a.sort_mode = 1
        a.apply_sort()
        self.assertEqual(self.titles(a)[a.sel], "Charlie")

    def test_groups_get_a_header_each_with_counts(self):
        a = make(list(PAGES))
        a.group = True
        a.apply_sort()
        headers = [text for kind, text in a.build_display() if kind == "h"]
        self.assertEqual(headers, ["◐ Doing (1)", "○ To do (2)", "✓ Done (1)"])
        self.assertEqual(sum(1 for kind, _ in a.build_display() if kind == "r"), 4)


class Unread(unittest.TestCase):
    def test_only_edits_after_the_last_look_are_unread(self):
        a = make(list(PAGES))
        a.baseline_seen()
        self.assertFalse(any(a.unread(p) for p in a.pages))  # first sight counts as seen
        edited = dict(a.pages[0], last_edited_time="2026-10-09T00:00:00Z")
        self.assertTrue(a.unread(edited))
        a.mark_seen(edited)
        self.assertFalse(a.unread(edited))
