"""Tests for the second round of UI tasks (R1 to R6 in TASKS.md)."""
import copy
import curses
import unittest

from herdr_notion import delegate as dg
from herdr_notion import notion as nt
from tests.support import make_app, page

PAGES = [page(0, "Bravo", "To do", "Low", "2026-10-01T00:00:00Z"), page(1, "Alpha", "Done", "High", "2026-10-03T00:00:00Z"),
         page(2, "Charlie", "In progress", "High", "2026-10-02T00:00:00Z"), page(3, "Delta", "To do", "Low", "2026-10-04T00:00:00Z"),
         page(4, "Echo", "Review", "High", "2026-10-05T00:00:00Z")]


def fresh(**kw):
    pages = copy.deepcopy(PAGES)
    for i, p in enumerate(pages):
        p["properties"]["ID"] = {"type": "rich_text", "rich_text": [{"plain_text": "ET-%d" % (100 + i)}]}
    return make_app(pages, **kw)


def agent(status="working", title="", name="claude", pane="w1:p1"):
    return {"pane_id": pane, "name": name, "agent": "claude", "status": status, "cwd": "/x", "title": title}


class AgentMatching(unittest.TestCase):
    def test_ticket_id_is_read_from_a_text_or_unique_id_property(self):
        app, _ = fresh()
        self.assertEqual(nt.page_ticket_id(app.pages[0]), "ET-%d" % (100 + int(app.pages[0]["id"][1:])))
        uid = {"properties": {"ID": {"type": "unique_id", "unique_id": {"prefix": "ET", "number": 7}}}}
        self.assertEqual(nt.page_ticket_id(uid), "ET-7")
        self.assertEqual(nt.page_ticket_id({"properties": {"Note": {"type": "rich_text", "rich_text": [{"plain_text": "hello"}]}}}), "")

    def test_match_by_ticket_id_in_the_agent_title_and_not_by_a_longer_number(self):
        pg = {"properties": {"ID": {"type": "rich_text", "rich_text": [{"plain_text": "ET-25"}]},
                             "Name": {"type": "title", "title": [{"plain_text": "x"}]}}}
        self.assertTrue(nt.match_agent(pg, [agent(title="ET-25 fix the thing")]))
        self.assertIsNone(nt.match_agent(pg, [agent(title="ET-2546 something else")]))

    def test_match_by_a_long_page_title(self):
        pg = {"properties": {"Name": {"type": "title", "title": [{"plain_text": "Fix login redirect"}]}}}
        self.assertTrue(nt.match_agent(pg, [agent(title="working on: fix login redirect now")]))
        short = {"properties": {"Name": {"type": "title", "title": [{"plain_text": "Fix"}]}}}
        self.assertIsNone(nt.match_agent(short, [agent(title="fix everything")]))

    def test_list_agents_carries_the_terminal_title(self):
        resp = {"result": {"agents": [{"pane_id": "w1:p1", "agent": "claude", "agent_status": "idle", "cwd": "/a",
                                       "terminal_title_stripped": "ET-1 demo"}]}}
        self.assertEqual(dg.list_agents(lambda a: resp)[0]["title"], "ET-1 demo")

    def test_a_working_match_is_tracked_and_its_end_is_offered(self):
        app, _ = fresh()
        pid = app.pages[0]["id"]
        tid = nt.page_ticket_id(app.pages[0])
        app.save_delegations = lambda: None
        app.on_agents([agent("working", title="%s fix" % tid)])
        self.assertIn(pid, app.delegations)
        self.assertTrue(app.delegations[pid]["auto"])
        app.on_agents([agent("idle", title="%s fix" % tid)])
        self.assertEqual(app.finished, [pid])

    def test_an_idle_match_only_gets_a_badge(self):
        app, scr = fresh(h=20, w=100)
        tid = nt.page_ticket_id(app.pages[0])
        app.save_delegations = lambda: None
        app.on_agents([agent("idle", title="%s notes" % tid, name="codex")])
        self.assertEqual(app.delegations, {})
        app.draw_list()
        self.assertTrue(scr.has("AGENT"))
        self.assertTrue(scr.has("○ codex"))

    def test_herdr_missing_is_quiet_and_stops_asking(self):
        app, _ = fresh()
        orig = dg.list_agents
        try:
            def boom():
                raise dg.HerdrError("no herdr")
            dg.list_agents = boom
            self.assertIsNone(app.quiet_agents())
            self.assertFalse(app.herdr_ok)
            app.on_agents(None)
            self.assertEqual(app.msg, "")
        finally:
            dg.list_agents = orig


from datetime import datetime, timezone

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


class QuickFilters(unittest.TestCase):
    def setup_app(self):
        app, scr = fresh(h=30, w=100)
        schema = copy.deepcopy(app.client.discover())
        schema["properties"]["Assignee"] = {"type": "people", "people": {}}
        schema["properties"]["Due"] = {"type": "date", "date": {}}
        app.client.discover = lambda: schema
        app.now = lambda: NOW
        edits = ["2026-11-04T09:00:00Z", "2026-11-02T09:00:00Z", "2026-10-01T09:00:00Z", "2026-11-04T11:00:00Z", "2026-10-15T09:00:00Z"]
        for p, e in zip(app.all, edits):
            p["last_edited_time"] = e
        by = {nt.page_title(p): p for p in app.all}
        by["Bravo"]["properties"]["Assignee"] = {"type": "people", "people": [{"id": "u", "name": "Ada"}]}
        by["Bravo"]["properties"]["Due"] = {"type": "date", "date": {"start": "2026-11-06", "end": None}}
        by["Echo"]["properties"]["Due"] = {"type": "date", "date": {"start": "2026-11-20", "end": None}}
        by["Alpha"]["properties"]["Due"] = {"type": "date", "date": {"start": "2026-11-05", "end": None}}  # Done: not due
        app.refilter()
        return app, scr

    def shown(self, app, q):
        app.quick = q
        app.refilter()
        return sorted(nt.page_title(p) for p in app.pages)

    def test_changed_today_and_this_week_follow_the_edit_time(self):
        app, _ = self.setup_app()
        self.assertEqual(len(app.pages), 5)
        self.assertEqual(len(self.shown(app, "today")), 2)
        self.assertEqual(len(self.shown(app, "week")), 3)

    def test_no_assignee_uses_the_people_property(self):
        app, _ = self.setup_app()
        shown = self.shown(app, "noassignee")
        self.assertNotIn("Bravo", shown)
        self.assertEqual(len(shown), 4)

    def test_due_this_week_skips_far_dates_and_finished_pages(self):
        app, _ = self.setup_app()
        self.assertEqual(self.shown(app, "due"), ["Bravo"])

    def test_unread_uses_the_seen_file(self):
        app, _ = self.setup_app()
        app.seen = {p["id"]: p["last_edited_time"] for p in app.all}
        app.seen[app.all[0]["id"]] = "2000-01-01T00:00:00Z"
        self.assertEqual(len(self.shown(app, "unread")), 1)

    def test_picker_sets_the_filter_shows_it_in_the_header_and_esc_clears_it(self):
        app, scr = self.setup_app()
        app.popup = lambda title, items, start=0: 1  # "Changed today"
        app.list_key(ord("Q"))
        self.assertEqual(app.quick, "today")
        scr.clear()
        app.draw_list()
        self.assertTrue(scr.has("changed today"))
        app.list_key(27)
        self.assertEqual(app.quick, "")
        self.assertEqual(len(app.pages), 5)

    def test_switching_tab_drops_the_quick_filter(self):
        app, _ = self.setup_app()
        app.quick = "today"
        app.refresh_async = lambda *a, **k: None
        app.pick_filter(0)
        self.assertEqual(app.quick, "")
