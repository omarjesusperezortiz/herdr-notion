import unittest

from herdr_notion import notion as nt


def rt(text):
    return [{"plain_text": text}]


def block(bid, t, text="", children=False, **extra):
    v = {"rich_text": rt(text)}
    v.update(extra)
    return {"id": bid, "type": t, t: v, "has_children": children}


class FlattenBlocks(unittest.TestCase):
    def test_basic_types(self):
        blocks = [
            block("1", "heading_2", "Plan"),
            block("2", "paragraph", "Hello"),
            block("3", "bulleted_list_item", "one"),
            block("4", "numbered_list_item", "first"),
            block("5", "numbered_list_item", "second"),
            block("6", "to_do", "ship", checked=True),
            block("7", "quote", "wise"),
            block("8", "code", "print(1)", language="python"),
            {"id": "9", "type": "divider", "divider": {}},
        ]
        self.assertEqual(nt.flatten_blocks(blocks), [
            "## Plan", "Hello", "- one", "1. first", "2. second", "[x] ship",
            "> wise", "```python", "print(1)", "```", "---"])

    def test_nested_children_are_indented(self):
        parent = block("p", "bulleted_list_item", "parent", children=True)
        kids = {"p": [block("c", "bulleted_list_item", "child")]}
        self.assertEqual(nt.flatten_blocks([parent], children=kids), ["- parent", "  - child"])

    def test_numbering_restarts_after_other_block(self):
        blocks = [block("1", "numbered_list_item", "a"), block("2", "paragraph", "x"),
                  block("3", "numbered_list_item", "b")]
        self.assertEqual(nt.flatten_blocks(blocks), ["1. a", "x", "1. b"])

    def test_unknown_block_without_text_is_skipped(self):
        self.assertEqual(nt.flatten_blocks([{"id": "x", "type": "table_of_contents", "table_of_contents": {}}]), [])


class Properties(unittest.TestCase):
    PAGE = {
        "id": "p1", "url": "https://www.notion.so/Fix-bug-p1",
        "properties": {
            "Name": {"type": "title", "title": rt("Fix bug")},
            "Status": {"type": "status", "status": {"name": "In progress"}},
            "Assignee": {"type": "people", "people": [{"id": "u1", "name": "Ada"}]},
            "Due": {"type": "date", "date": {"start": "2026-01-02", "end": None}},
            "Tags": {"type": "multi_select", "multi_select": [{"name": "a"}, {"name": "b"}]},
            "Empty": {"type": "rich_text", "rich_text": []},
            "ID": {"type": "unique_id", "unique_id": {"prefix": "TASK", "number": 7}},
        },
    }

    def test_title_and_properties(self):
        self.assertEqual(nt.page_title(self.PAGE), "Fix bug")
        self.assertEqual(nt.page_properties(self.PAGE), [
            ("Status", "In progress"), ("Assignee", "Ada"), ("Due", "2026-01-02"),
            ("Tags", "a, b"), ("ID", "TASK-7")])

    def test_title_property_name(self):
        self.assertEqual(nt.title_property_name(self.PAGE["properties"]), "Name")


class Filters(unittest.TestCase):
    def test_no_filter(self):
        self.assertEqual(nt.build_query({"name": "All"}, page_size=10), {"page_size": 10})

    def test_me_placeholder_and_sort(self):
        flt = {"name": "Mine", "filter": {"property": "Assignee", "people": {"contains": "{me}"}},
               "sort": {"property": "Last edited", "direction": "ascending"}}
        q = nt.build_query(flt, me="user-123")
        self.assertEqual(q["filter"], {"property": "Assignee", "people": {"contains": "user-123"}})
        self.assertEqual(q["sorts"], [{"property": "Last edited", "direction": "ascending"}])

    def test_search_alone_and_combined(self):
        alone = nt.build_query({"name": "All"}, query="bug", title_property="Name")
        self.assertEqual(alone["filter"], {"property": "Name", "title": {"contains": "bug"}})
        flt = {"name": "x", "filter": {"property": "Status", "status": {"equals": "Done"}}}
        both = nt.build_query(flt, query="bug", title_property="Name")
        self.assertEqual(len(both["filter"]["and"]), 2)

    def test_substitution_reaches_nested_lists(self):
        out = nt.substitute({"and": [{"a": "{me}"}]}, {"me": "u"})
        self.assertEqual(out, {"and": [{"a": "u"}]})

    def test_does_not_mutate_config(self):
        flt = {"name": "m", "filter": {"x": "{me}"}}
        nt.build_query(flt, me="u")
        self.assertEqual(flt["filter"], {"x": "{me}"})

    def test_cursor(self):
        self.assertEqual(nt.build_query({}, cursor="abc")["start_cursor"], "abc")


class Status(unittest.TestCase):
    SCHEMA = {"properties": {
        "Status": {"type": "status", "status": {"options": [{"name": "To do"}, {"name": "Done"}]}},
        "Name": {"type": "title", "title": {}}}}

    def test_options(self):
        self.assertEqual(nt.status_options(self.SCHEMA, "Status"), ("status", ["To do", "Done"]))

    def test_wrong_property_raises(self):
        with self.assertRaises(nt.NotionError):
            nt.status_options(self.SCHEMA, "Name")

    def test_patch_body(self):
        self.assertEqual(nt.status_patch("Status", "status", "Done"),
                         {"properties": {"Status": {"status": {"name": "Done"}}}})


class Prompt(unittest.TestCase):
    PAGE = Properties.PAGE

    def test_fills_placeholders(self):
        out = nt.render_prompt("T={title} U={url} S={status}\n{properties}\n{body}", self.PAGE, "text here")
        self.assertIn("T=Fix bug", out)
        self.assertIn("U=https://www.notion.so/Fix-bug-p1", out)
        self.assertIn("S=In progress", out)
        self.assertIn("- Assignee: Ada", out)
        self.assertTrue(out.endswith("text here"))

    def test_body_braces_survive(self):
        out = nt.render_prompt("{body}", self.PAGE, "use {title} literally")
        self.assertEqual(out, "use {title} literally")

    def test_truncates_long_body(self):
        out = nt.render_prompt("{body}", self.PAGE, "x" * 50, max_body_chars=10)
        self.assertEqual(out, "x" * 10 + "\n[truncated]")


if __name__ == "__main__":
    unittest.main()
