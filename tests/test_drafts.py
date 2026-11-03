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


if __name__ == "__main__":
    unittest.main()
