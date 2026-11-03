import os
import tempfile
import unittest

from herdr_notion import drafts as dr
from herdr_notion import newticket as nf
from tests.support import SCHEMA, make_app, page

BACK = __import__("curses").KEY_BTAB
ENTER = "\n"
SCHEMA2 = {"title": SCHEMA["title"], "properties": dict(SCHEMA["properties"], Project={"type": "relation", "relation": {"data_source": "t"}},
                                                         Branch={"type": "formula"})}


class Files(unittest.TestCase):
    def setUp(self):
        self.d = os.path.join(tempfile.mkdtemp(), "drafts")

    def test_write_list_remove(self):
        path = dr.write_draft(self.d, "Fix the route", {"Priority": "High"}, "text", "maybe a duplicate", "orchestrator")
        got = dr.list_drafts(self.d)
        self.assertEqual([p for p, _ in got], [path])
        self.assertEqual(got[0][1]["title"], "Fix the route")
        self.assertEqual(got[0][1]["set"], {"Priority": "High"})
        dr.remove_draft(path)
        self.assertEqual(dr.list_drafts(self.d), [])

    def test_a_draft_needs_a_title_and_junk_is_skipped(self):
        with self.assertRaises(ValueError):
            dr.write_draft(self.d, "  ")
        os.makedirs(self.d, exist_ok=True)
        open(os.path.join(self.d, "bad.json"), "w").write("{not json")
        open(os.path.join(self.d, ".part-x"), "w").write("{}")
        open(os.path.join(self.d, "notitle.json"), "w").write('{"title": ""}')
        self.assertEqual(dr.list_drafts(self.d), [])

    def test_oldest_first(self):
        a = dr.write_draft(self.d, "A")
        b = dr.write_draft(self.d, "B")
        self.assertEqual([p for p, _ in dr.list_drafts(self.d)], [a, b])

    def test_the_command_writes_a_draft_and_says_nothing_went_to_notion(self):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            code = dr.main(["--title", "T", "--set", "Priority=High", "--set", "Project=Checkout", "--note", "n", "--from", "o", "--to", self.d])
        self.assertEqual(code, 0)
        self.assertIn("Nothing was written to Notion", out.getvalue())
        d = dr.list_drafts(self.d)[0][1]
        self.assertEqual((d["set"], d["from"], d["note"]), ({"Priority": "High", "Project": "Checkout"}, "o", "n"))

    def test_matching(self):
        self.assertEqual(nf.match_option(["In progress", "Done"], " in PROGRESS "), "In progress")
        self.assertIsNone(nf.match_option(["Done"], "urgent"))
        found = [{"title": "Checkout", "url": "u1"}, {"title": "Checkout v2", "url": "u2"}]
        self.assertEqual(nf.match_relation(found, "checkout")["url"], "u1")
        self.assertIsNone(nf.match_relation(found, "check"))  # two results, neither exact
        self.assertEqual(nf.match_relation(found[:1], "chec")["url"], "u1")


class Reviewing(unittest.TestCase):
    def app(self):
        state = tempfile.mkdtemp()
        os.environ["HERDR_PLUGIN_STATE_DIR"] = state
        app, scr = make_app([page(0, "Alpha")], state_dir=state)
        app.client.discover = lambda: SCHEMA2
        app.client.relation_options = lambda prop, query="", limit=30: [{"title": "Checkout", "url": "https://x/proj"}]
        app.cfg = dict(app.cfg, filters=[{"name": "All"}], new_fields=[])
        app.fidx = 0
        return app, scr, dr.drafts_dir()

    def test_a_draft_opens_filled_in_and_creates_once_confirmed(self):
        app, scr, d = self.app()
        dr.write_draft(d, "Fix the route", {"status": "in progress", "Priority": "High", "Project": "checkout", "Branch": "x", "Nope": "y"},
                       "Steps here", "possible duplicate", "orchestrator")
        scr.keys = [BACK, ENTER]
        app.open_drafts()
        w = app.client.writes[-1]
        self.assertEqual(w[1], "Fix the route")
        self.assertEqual(w[2], {"Status": "In progress", "Priority": "High", "Project": ["https://x/proj"]})
        self.assertEqual(w[3], "Steps here")
        self.assertEqual(dr.list_drafts(d), [])  # used up

    def test_the_box_says_who_and_why(self):
        app, scr, d = self.app()
        dr.write_draft(d, "T", {}, "", "possible duplicate: ET-1", "orchestrator")
        scr.keys = ["\x1b"]
        app.open_drafts()
        self.assertTrue(scr.has("orchestrator"))
        self.assertTrue(scr.has("possible duplicate: ET-1"))
        self.assertEqual(len(dr.list_drafts(d)), 1)  # Esc keeps it for later

    def test_an_option_that_does_not_exist_blocks_the_write(self):
        app, scr, d = self.app()
        dr.write_draft(d, "T", {"Priority": "Urgent"})
        scr.keys = [BACK, ENTER, "\x04"]
        said, real = [], app.toast
        app.toast = lambda text, *a, **k: (said.append(text), real(text, *a, **k))
        app.open_drafts()
        self.assertEqual(app.client.writes, [])
        self.assertTrue(any("not one of its options" in t for t in said), said)
        self.assertEqual(dr.list_drafts(d), [])  # ^D discards

    def test_a_new_draft_is_announced_and_counted(self):
        app, scr, d = self.app()
        app.draft_count, app.next_draft_poll = 0, 0
        dr.write_draft(d, "Found a bug", {}, "", "", "api-worker")
        app.poll_drafts()
        self.assertIn("api-worker drafted", app.toast_state["text"])
        self.assertEqual(app.draft_count, 1)
        app.redraw()
        self.assertTrue(scr.has("N 1 draft"))

    def test_no_drafts_says_so(self):
        app, scr, d = self.app()
        app.open_drafts()
        self.assertEqual(app.toast_state["text"], "no drafts waiting")



from unittest import mock

from herdr_notion import delegate as dg

AGENTS = [{"pane_id": "w1", "name": "api-worker", "agent": "claude", "status": "working", "cwd": "/x", "title": "busy"},
          {"pane_id": "w2", "name": "orchestrator", "agent": "claude", "status": "idle", "cwd": "/y", "title": ""}]


class Asking(unittest.TestCase):
    def app(self):
        state = tempfile.mkdtemp()
        os.environ["HERDR_PLUGIN_STATE_DIR"] = state
        app, scr = make_app([page(0, "Alpha")], state_dir=state)
        app.client.discover = lambda: SCHEMA2
        app.client.relation_options = lambda prop, query="", limit=30: [{"title": "Checkout", "url": "https://x/proj"}]
        app.cfg = dict(app.cfg, filters=[{"name": "All"}], new_fields=[])
        app.fidx = 0
        return app, scr

    def test_the_orchestrator_is_offered_first(self):
        self.assertEqual([a["name"] for a in nf.orchestrator_first(AGENTS)], ["orchestrator", "api-worker"])
        self.assertEqual([a["name"] for a in nf.orchestrator_first(AGENTS, "api-worker")], ["api-worker", "orchestrator"])
        self.assertEqual([a["name"] for a in nf.orchestrator_first(AGENTS[:1])], ["api-worker"])

    def test_the_prompt_has_the_request_the_choices_and_the_command(self):
        text = nf.agent_prompt("route is empty", "Tickets", [("Priority", "select", ["High", "Low"]), ("Project", "relation", ["A", "B"]),
                                                              ("SPs", "number", [])], "run-me --title ...", {"Project"})
        for part in ("route is empty", "Priority: High | Low", "Project: A | B | ... (more exist)", "SPs: a number", "run-me --title",
                     "Do not write to Notion yourself"):
            self.assertIn(part, text)

    def test_describe_pick_send(self):
        app, scr = self.app()
        sent = []
        with mock.patch.object(dg, "list_agents", lambda: [dict(a) for a in AGENTS]), mock.patch.object(dg, "send_prompt", lambda pane, text: sent.append((pane, text))):
            scr.keys = ["\x01"] + list("empty route after renewing") + ["\n"]
            app.new_page()
        self.assertEqual(sent[0][0], "w2")  # the orchestrator, first in the list
        self.assertIn("empty route after renewing", sent[0][1])
        self.assertIn("Priority: High | Low", sent[0][1])
        self.assertIn("Project: Checkout", sent[0][1])
        self.assertIn("python3 -m herdr_notion draft --to", sent[0][1])
        self.assertEqual(app.client.writes, [])  # nothing written to Notion
        self.assertIn("w2", app.asks)

    def test_nothing_is_sent_without_a_description_and_the_form_comes_back(self):
        app, scr = self.app()
        sent = []
        with mock.patch.object(dg, "list_agents", lambda: [dict(a) for a in AGENTS]), mock.patch.object(dg, "send_prompt", lambda pane, text: sent.append(pane)):
            scr.keys = ["\x01", "\n", "\x01"] + list("Back in the form") + [BACK, "\n"]
            app.new_page()
        self.assertEqual(sent, [])
        self.assertEqual(app.client.writes[-1][1], "Back in the form")

    def test_the_pick_moves_down_the_list(self):
        app, scr = self.app()
        sent = []
        with mock.patch.object(dg, "list_agents", lambda: [dict(a) for a in AGENTS]), mock.patch.object(dg, "send_prompt", lambda pane, text: sent.append(pane)):
            scr.keys = ["\x01"] + list("x") + [__import__("curses").KEY_DOWN, "\n"]
            app.new_page()
        self.assertEqual(sent, ["w1"])

    def test_herdr_not_reachable_keeps_the_form(self):
        app, scr = self.app()

        def boom():
            raise dg.HerdrError("no socket")
        with mock.patch.object(dg, "list_agents", boom):
            scr.keys = ["\x01"] + list("Still here") + [BACK, "\n"]
            app.new_page()
        self.assertEqual(app.client.writes[-1][1], "Still here")

    def test_an_answer_clears_the_wait_and_silence_says_so(self):
        app, scr = self.app()
        d = dr.drafts_dir()
        app.asks = {"w2": {"name": "orchestrator", "text": "x", "at": 0}}
        app.draft_count, app.next_draft_poll = 0, 0
        dr.write_draft(d, "T", {}, "", "", "orchestrator")
        app.poll_drafts()
        self.assertEqual(app.asks, {})
        import time
        app.asks = {"w1": {"name": "api-worker", "text": "x", "at": time.time() - 1000}}
        app.next_draft_poll = 0
        app.poll_drafts()
        self.assertIn("no draft from api-worker yet", app.toast_state["text"])
        self.assertEqual(app.asks, {})



class LongAgentList(unittest.TestCase):
    def test_the_picked_agent_stays_in_view_with_thirty_agents(self):
        state = tempfile.mkdtemp()
        os.environ["HERDR_PLUGIN_STATE_DIR"] = state
        app, scr = make_app([page(0, "Alpha")], h=30, state_dir=state)
        agents = [{"pane_id": "w%d" % i, "name": "agent-%d" % i, "agent": "claude", "status": "idle", "cwd": "/x", "title": "t%d" % i} for i in range(30)]
        app.cfg = dict(app.cfg, filters=[{"name": "All"}], new_fields=[])
        app.fidx = 0
        with mock.patch.object(dg, "list_agents", lambda: agents):
            app.enter_ask({"fields": [], "body": ""} | {})
        form = {"mode": "ask", "ask": {"text": "x", "sel": 20, "agents": agents}}
        app.draw_ask(form)
        self.assertTrue(scr.has("▸ ✓ agent-20"))
        self.assertTrue(scr.has("more"))
        self.assertTrue(scr.has("Esc cancel"))  # the box did not push its own footer out


if __name__ == "__main__":
    unittest.main()
