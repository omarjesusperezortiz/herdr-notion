import os
import tempfile
import time
import unittest
from unittest import mock

from herdr_notion import mcpbackend as mb
from herdr_notion import notion as nt
from tests.support import make_app, page

PAGES = [page(0, "Alpha", "To do", "Low"), page(1, "Bravo", "In progress", "High")]


def settle(app):
    for _ in range(100):
        app.poll_job()
        if app.job is None:
            return
        time.sleep(0.02)


class Editing(unittest.TestCase):
    def app(self):
        app, scr = make_app([dict(p) for p in PAGES])
        app.confirm = lambda text: True
        return app

    def test_editing_a_select_writes_the_option_after_confirming(self):
        app = self.app()
        answers = iter([next(i for i, n in enumerate(self.editable(app)) if n.startswith("Priority")), 0])  # Priority, then "High"
        app.popup = lambda title, items, start=0: next(answers)
        app.edit_property()
        self.assertEqual(app.client.writes, [("property", "p0", "Priority", "High")])
        self.assertEqual(nt.prop_text(app.pages[app.sel]["properties"]["Priority"]), "High")
        self.assertIn("Priority set to High", app.msg)

    def editable(self, app):
        schema = app.client.discover()
        return [n for n, p in schema["properties"].items() if p["type"] in app.EDITABLE]

    def test_declining_the_confirm_writes_nothing(self):
        app = self.app()
        app.confirm = lambda text: False
        answers = iter([0, 0])
        app.popup = lambda title, items, start=0: next(answers)
        app.edit_property()
        self.assertEqual(app.client.writes, [])
        self.assertEqual(app.msg, "cancelled")

    def test_a_select_can_be_cleared(self):
        app = self.app()
        names = self.editable(app)
        answers = iter([names.index("Priority"), 2])  # "(clear)" is after the two options
        app.popup = lambda title, items, start=0: next(answers)
        app.edit_property()
        self.assertEqual(app.client.writes[-1], ("property", "p0", "Priority", None))


class Comments(unittest.TestCase):
    def test_comments_load_with_the_page_and_show_under_the_body(self):
        app, scr = make_app([dict(p) for p in PAGES], h=30, w=100)
        app.client.comment_list = [{"id": "c1", "author": "Grace", "when": "2026-10-30T09:00:00Z", "text": "Can we ship behind a flag?"}]
        app.open_page(app.pages[0])
        settle(app)
        app.draw_detail()
        self.assertTrue(scr.has("Comments (1)"))
        self.assertTrue(scr.has("Grace"))
        self.assertTrue(scr.has("ship behind a flag"))

    def test_posting_a_comment_asks_first_then_reloads_the_list_of_comments(self):
        app, scr = make_app([dict(p) for p in PAGES])
        app.open_page(app.pages[0])
        settle(app)
        app.ask = lambda label, initial="": "Looks good to me"
        app.confirm = lambda text: True
        app.comment()
        self.assertEqual(app.client.writes, [("comment", "p0", "Looks good to me")])
        self.assertEqual(app.detail["comments"][-1]["text"], "Looks good to me")

    def test_no_comment_is_posted_without_confirming(self):
        app, _ = make_app([dict(p) for p in PAGES])
        app.open_page(app.pages[0])
        settle(app)
        app.ask = lambda label, initial="": "hello"
        app.confirm = lambda text: False
        app.comment()
        self.assertEqual(app.client.writes, [])

    def test_the_comment_xml_is_parsed(self):
        class Fake:
            def call(self, name, args=None):
                if name == "notion-get-users":
                    return {"results": [{"id": "u1", "name": "Ada"}]}
                return {"text": '<discussions><discussion id="d1"><comment id="c2" url="https://x/p/1?d=1" user-url="user://u1" '
                                'datetime="2026-10-30T10:00:00.000Z">Second &amp; last</comment>'
                                '<comment id="c1" url="https://x/p/1?d=1" user-url="user://u1" datetime="2026-10-30T09:00:00.000Z">First</comment>'
                                '</discussion></discussions>'}
        cl = mb.McpClient("ds", Fake())
        got = cl.comments("https://x/p/1")
        self.assertEqual([c["text"] for c in got], ["First", "Second & last"])  # oldest first
        self.assertEqual(got[0]["author"], "Ada")


class Creating(unittest.TestCase):
    def test_a_new_page_goes_to_the_top_after_confirming(self):
        app, _ = make_app([dict(p) for p in PAGES])
        app.ask = lambda label, initial="": "Write the release notes"
        app.popup = lambda title, items, start=0: 1  # the first real status
        app.confirm = lambda text: True
        app.new_page()
        self.assertEqual(app.client.writes[-1][0:2], ("create", "Write the release notes"))
        self.assertEqual(nt.page_title(app.pages[0]), "Write the release notes")
        self.assertEqual(app.sel, 0)

    def test_nothing_is_created_without_a_title_or_a_confirm(self):
        app, _ = make_app([dict(p) for p in PAGES])
        app.ask = lambda label, initial="": ""
        app.new_page()
        self.assertEqual(app.client.writes, [])
        app.ask = lambda label, initial="": "Something"
        app.popup = lambda title, items, start=0: 0
        app.confirm = lambda text: False
        app.new_page()
        self.assertEqual(app.client.writes, [])


class Databases(unittest.TestCase):
    def test_switching_keeps_each_databases_own_config(self):
        cfg_dir = tempfile.mkdtemp()
        os.environ["HERDR_PLUGIN_CONFIG_DIR"] = cfg_dir
        self.addCleanup(lambda: os.environ.pop("HERDR_PLUGIN_CONFIG_DIR", None))
        os.makedirs(os.path.join(cfg_dir, "databases"))
        with open(os.path.join(cfg_dir, "config.toml"), "w") as f:
            f.write('[notion]\n# Sprint board\ndata_source_id = "ds"\n')
        with open(os.path.join(cfg_dir, "databases", "other.toml"), "w") as f:
            f.write('[notion]\n# Docs\ndata_source_id = "other"\n')
        app, _ = make_app([dict(p) for p in PAGES])
        seen = {}

        def popup(title, items, start=0):
            seen["items"] = items
            return 1  # the other database
        app.popup = popup
        app.load_config = lambda: None
        app.refresh_async = lambda label="": seen.setdefault("loading", label)
        app.switch_database()
        self.assertEqual(seen["items"], ["● Demo", "Docs", "+ add another database…"])  # the current one by its live name
        self.assertIn('data_source_id = "other"', open(os.path.join(cfg_dir, "config.toml")).read())
        self.assertTrue(os.path.exists(os.path.join(cfg_dir, "databases", "ds.toml")))  # the one we left is remembered
        self.assertIn("Loading", seen["loading"])


if __name__ == "__main__":
    unittest.main()


class CommentText(unittest.TestCase):
    RAW = ('<discussions><discussion id="d1"><comment id="c1" url="https://x/p/1?d=1" user-url="user://u1" datetime="2026-10-30T09:00:00.000Z">'
           '<b>Title:</b> <br />Session does not appear<br /><br /><b>Steps:</b><br />1. Open <code>one_time_only</code>'
           '</comment><comment id="c2" url="https://x/p/1?d=1" user-url="user://u1" datetime="2026-10-30T10:00:00.000Z">'
           '<mention url="user://u2" /></comment></discussion></discussions>')

    def client(self):
        class Fake:
            def call(self, name, args=None):
                if name == "notion-get-users":
                    return {"results": [{"id": args["user_id"], "name": {"u1": "Ada", "u2": "Grace"}[args["user_id"]]}]}
                return {"text": CommentText.RAW}
        return mb.McpClient("ds", Fake())

    def test_formatting_tags_and_breaks_are_cleaned(self):
        got = self.client().comments("https://x/p/1")
        self.assertNotIn("<", got[0]["text"])
        self.assertEqual(got[0]["text"].split("\n"), ["Title:", "Session does not appear", "", "Steps:", "1. Open one_time_only"])
        self.assertIn("Title:", got[0]["text"])
        self.assertIn("1. Open one_time_only", got[0]["text"])

    def test_a_mention_reads_as_the_persons_name(self):
        got = self.client().comments("https://x/p/1")
        self.assertEqual(got[1]["text"], "@Grace")
        self.assertEqual(got[1]["author"], "Ada")

    def test_the_page_draws_comments_without_any_markup(self):
        app, scr = make_app([dict(p) for p in PAGES], h=30, w=100)
        app.client.comment_list = self.client().comments("https://x/p/1")
        app.open_page(app.pages[0])
        settle(app)
        app.draw_detail()
        text = scr.text()
        self.assertIn("Comments (2)", text)
        self.assertNotIn("<br", text)
        self.assertNotIn("<b>", text)
        self.assertIn("@Grace", text)
