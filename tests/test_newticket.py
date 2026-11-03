import curses
import unittest

from herdr_notion import newticket as nf
from herdr_notion import notion as nt
from tests.support import make_app, page

REAL = {"properties": {
    "Name": {"type": "title"}, "Status": {"type": "status"}, "Assignee": {"type": "relation"}, "Project": {"type": "relation"},
    "Type": {"type": "relation"}, "Priority": {"type": "select"}, "Sprints": {"type": "relation"}, "Due": {"type": "date"},
    "SPs": {"type": "number"}, "State": {"type": "formula"}, "Branch": {"type": "formula"}, "Id": {"type": "rich_text"}}}
ME = "https://example.test/people/me"
MINE = {"and": [{"property": "Assignee", "relation": {"contains": ME}}, {"property": "Status", "status": {"does_not_equal": "Deployed"}}]}
TAB = "\t"
ENTER = "\n"
BACK = curses.KEY_BTAB


def typed(text):
    return list(text)


class Logic(unittest.TestCase):
    def test_the_usual_fields_in_order_and_nothing_computed(self):
        names = [f["name"] for f in nf.default_fields(REAL, "Status")]
        self.assertEqual(names, ["Name", "Status", "Assignee", "Project", "Type", "Priority", "Sprints", "Due", "SPs"])

    def test_the_config_chooses_the_fields(self):
        names = [f["name"] for f in nf.default_fields(REAL, "Status", ["priority", "SPs", "State", "nope"])]
        self.assertEqual(names, ["Name", "Priority", "SPs"])  # a formula is never offered

    def test_a_tab_that_keeps_yours_fills_the_assignee(self):
        fields = nf.default_fields(REAL, "Status")
        fills = nf.tab_fills(MINE, fields, {ME: "Ada"})
        self.assertEqual(fills["Assignee"]["raw"], [ME])
        self.assertEqual(fills["Assignee"]["text"], "Ada")
        self.assertNotIn("Status", fills)  # "not Deployed" decides nothing to fill

    def test_listed_on_the_tab_only_when_it_would_pass_the_filter(self):
        fields = nf.default_fields(REAL, "Status")
        self.assertFalse(nf.still_listed(MINE, fields))  # no assignee
        fields[2]["raw"] = [ME]
        self.assertTrue(nf.still_listed(MINE, fields))
        fields[1]["raw"] = "Deployed"
        self.assertFalse(nf.still_listed(MINE, fields))
        formula = {"property": "Current sprint?", "formula": {"checkbox": {"equals": True}}}
        self.assertFalse(nf.still_listed(formula, fields))  # cannot be judged here

    def test_values_for_notions_create_call(self):
        fields = nf.default_fields(REAL, "Status")
        by = {f["name"]: f for f in fields}
        by["Status"].update(raw="In progress", text="In progress")
        by["Project"].update(raw=["https://x/p"], text="Checkout")
        by["Due"].update(raw="2026-11-08", text="2026-11-08")
        by["SPs"].update(raw="5", text="5")
        props, body = nf.property_values(fields[1:], REAL, "  text \n")
        self.assertEqual(props, {"Status": "In progress", "Project": ["https://x/p"], "date:Due:start": "2026-11-08",
                                 "date:Due:is_datetime": 0, "SPs": 5})
        self.assertEqual(body, "text")

    def test_typed_values_are_checked(self):
        self.assertEqual(nf.parse_value("number", " 3 "), ("3", ""))
        self.assertIn("not a number", nf.parse_value("number", "x")[1])
        self.assertIn("2026-11-08", nf.parse_value("date", "tomorrow")[1])
        self.assertEqual(nf.parse_value("date", ""), (None, ""))


class Form(unittest.TestCase):
    def app(self, filters=None):
        app, scr = make_app([page(0, "Alpha", "To do", "Low"), page(1, "Bravo", "In progress", "High")])
        app.cfg = dict(app.cfg, filters=filters or [{"name": "All"}], new_fields=[])
        app.fidx = 0
        return app, scr

    def test_a_title_and_create_writes_one_page(self):
        app, scr = self.app()
        scr.keys = typed("Write the release notes") + [BACK, ENTER]  # ⇧Tab from the title lands on [ Create ]
        app.new_page()
        self.assertEqual(app.client.writes[-1][:2], ("create", "Write the release notes"))
        self.assertEqual(nt.page_title(app.pages[0]), "Write the release notes")
        self.assertEqual(app.sel, 0)

    def test_the_form_is_a_box_with_the_fields(self):
        app, scr = self.app()
        scr.keys = ["\x1b"]
        app.new_page()
        for text in ("New ticket", "Status", "Priority", "Description", "[ Create ⏎ ]"):
            self.assertTrue(scr.has(text), text)

    def test_nothing_is_written_without_a_title(self):
        app, scr = self.app()
        scr.keys = [BACK, ENTER, "\x1b"]
        app.new_page()
        self.assertEqual(app.client.writes, [])
        self.assertEqual(app.toast_state["text"], "give it a title first")

    def test_picked_values_and_the_description_are_sent(self):
        app, scr = self.app()
        picks = iter([2, 1])  # Status: the second option; Priority: the first
        app.popup = lambda title, items, start=0, at=None: next(picks)
        scr.keys = typed("Fix it") + [TAB, ENTER, TAB, ENTER] + [TAB] * 0
        scr.keys += [TAB]  # past Priority to the description
        scr.keys += typed("line one") + [ENTER] + typed("two") + [TAB, ENTER]
        app.new_page()
        w = app.client.writes[-1]
        self.assertEqual(w[2], {"Status": "In progress", "Priority": "High"})
        self.assertEqual(w[3], "line one\ntwo")

    def test_the_tab_fills_what_it_keeps_and_a_tab_that_hides_it_says_so(self):
        app, scr = self.app([{"name": "Mine", "filter": {"property": "Priority", "select": {"equals": "High"}}}])
        scr.keys = typed("Urgent") + [BACK, ENTER]
        app.new_page()
        self.assertEqual(app.client.writes[-1][2], {"Priority": "High"})  # filled from the tab
        self.assertEqual(nt.page_title(app.pages[0]), "Urgent")  # and listed on it
        app2, scr2 = self.app([{"name": "Mine", "filter": {"property": "Priority", "select": {"equals": "High"}}}])
        scr2.keys = typed("Calm") + [TAB, TAB, ENTER, TAB, TAB, ENTER]  # to Priority, clear it, on to Create
        app2.popup = lambda title, items, start=0, at=None: 0
        app2.new_page()
        self.assertIn("does not list it", app2.toast_state["text"])
        self.assertEqual([nt.page_title(p) for p in app2.pages], ["Alpha", "Bravo"])  # no other row stands in for it

    def test_your_sort_and_grouping_survive(self):
        app, scr = self.app()
        app.group = True
        app.sort_mode = 1
        app.refilter()
        scr.keys = typed("New one") + [BACK, ENTER]
        app.new_page()
        self.assertTrue(app.group)
        self.assertEqual(app.sort_mode, 1)

    def test_a_failed_write_keeps_the_form_open_with_its_values(self):
        app, scr = self.app()
        calls = []

        def create(title_prop, title, props=None, body=""):
            calls.append(title)
            if len(calls) == 1:
                raise nt.NotionError("Notion said no")
            return page(9, title)
        app.client.create_page = create
        scr.keys = typed("Keep me") + [BACK, ENTER, ENTER]  # first Create fails; the cursor is still on Create, so ⏎ again
        app.new_page()
        self.assertEqual(calls, ["Keep me", "Keep me"])

    def test_create_another_opens_a_fresh_form(self):
        app, scr = self.app()
        scr.keys = typed("One") + ["\x0e"] + typed("Two") + ["\x0e", "\x1b"]
        app.new_page()
        self.assertEqual([w[1] for w in app.client.writes if w[0] == "create"], ["One", "Two"])

    def test_escape_asks_before_throwing_away_a_title(self):
        app, scr = self.app()
        scr.keys = typed("Half") + ["\x1b", "n", "\x1b", "y"]
        app.new_page()
        self.assertEqual(app.client.writes, [])
        self.assertEqual(scr.keys, [])


if __name__ == "__main__":
    unittest.main()
