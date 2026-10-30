import unittest

from herdr_notion import mcpbackend as mb
from herdr_notion import notion as nt

STATE = {"name": "Demo", "url": "collection://x", "schema": {
    "Name": {"name": "Name", "type": "title"},
    "Status": {"name": "Status", "type": "status", "groups": {
        "to_do": [{"name": "Not started"}], "in_progress": [{"name": "In progress"}], "complete": [{"name": "Deployed"}]}},
    "Priority": {"name": "Priority", "type": "select", "options": [{"name": "High"}, {"name": "Low"}]},
    "Owner": {"name": "Owner", "type": "person"},
    "Due": {"name": "Due", "type": "date"},
    "Updated at": {"name": "Updated at", "type": "last_edited_time"},
    "Current sprint?": {"name": "Current sprint?", "type": "formula"},
    "userDefined:Id": {"name": "Id", "type": "auto_increment_id"}}}


class SchemaAndRows(unittest.TestCase):
    def setUp(self):
        self.schema = mb.schema_from_state(STATE)

    def test_status_groups_become_rest_groups(self):
        self.assertEqual(nt.status_groups(self.schema, "Status"), {"Not started": "To-do", "In progress": "In progress", "Deployed": "Complete"})
        kind, options = nt.status_options(self.schema, "Status")
        self.assertEqual((kind, options), ("status", ["Not started", "In progress", "Deployed"]))

    def test_row_becomes_a_page(self):
        row = {"Name": "\\[Demo\\] task", "Status": "In progress", "Priority": "High", "Owner": "[\"u1\"]",
               "date:Due:start": "2026-11-01", "Updated at": "2026-10-30T15:30:16.703Z", "Current sprint?": "formulaResult://a/b/c",
               "url": "https://x/p/1?pvs=4"}
        page = mb.to_page(row, self.schema, {"u1": "Ada"})
        self.assertEqual(nt.page_title(page), "[Demo] task")
        self.assertEqual(nt.prop_text(page["properties"]["Status"]), "In progress")
        self.assertEqual(nt.prop_text(page["properties"]["Owner"]), "Ada")
        self.assertEqual(nt.prop_text(page["properties"]["Due"]), "2026-11-01")
        self.assertNotIn("Current sprint?", page["properties"])  # formula results are references, not values
        self.assertEqual(page["last_edited_time"], "2026-10-30T15:30:16.703Z")
        self.assertEqual(page["id"], "https://x/p/1?pvs=4")
        self.assertEqual(page["url"], "https://x/p/1")


class Filters(unittest.TestCase):
    def setUp(self):
        self.schema = mb.schema_from_state(STATE)

    def test_status_and_select(self):
        f = mb.to_mcp_filter({"and": [{"property": "Status", "status": {"does_not_equal": "Deployed"}},
                                       {"property": "Priority", "select": {"equals": "High"}}]}, self.schema)
        self.assertEqual(f["type"], "group")
        self.assertEqual(f["filters"][0]["operator"], "status_is_not")
        self.assertEqual(f["filters"][0]["value"], {"type": "is_option", "value": "Deployed"})
        self.assertEqual(f["filters"][1]["operator"], "enum_is")

    def test_me_is_relative(self):
        f = mb.to_mcp_filter({"property": "Owner", "people": {"contains": "me"}}, self.schema)
        self.assertEqual((f["propertyType"], f["operator"], f["value"]), ("person", "person_contains", {"type": "relative", "value": "me"}))

    def test_formula_checkbox(self):
        f = mb.to_mcp_filter({"property": "Current sprint?", "formula": {"checkbox": {"equals": True}}}, self.schema)
        self.assertEqual(f["propertyType"], "formula")
        self.assertEqual(f["resultFilter"]["value"], {"type": "exact", "value": True})

    def test_title_search_and_unknown(self):
        f = mb.to_mcp_filter({"property": "Name", "title": {"contains": "login"}}, self.schema)
        self.assertEqual(f["operator"], "string_contains")
        with self.assertRaises(nt.NotionError):
            mb.to_mcp_filter({"property": "Nope", "select": {"equals": "x"}}, self.schema)


class Text(unittest.TestCase):
    def test_markdown_becomes_plain_lines(self):
        md = '<callout>\nHello<br />World\n</callout>\n\n## Goal\n\nSee [docs](http://x) and <mention url="u" />.'
        self.assertEqual(mb.markdown_lines(md), ["Hello", "World", "", "## Goal", "", "See docs and (link)."])

    def test_collection_id_and_tag_json(self):
        self.assertEqual(mb.collection_id("x <data-source url=\"{{collection://2efbd3bd-193b-80c5-8fed-000b400e4cd8}}\">"),
                         "2efbd3bd-193b-80c5-8fed-000b400e4cd8")
        self.assertEqual(mb.tag_json("<a>\n{\"k\": 1}\n</a>", "a"), {"k": 1})
        with self.assertRaises(nt.NotionError):
            mb.tag_json("nothing", "a")


class FakeMcp:
    def __init__(self):
        self.calls = []

    def call(self, name, args=None):
        self.calls.append((name, args))
        if name == "notion-fetch" and args["id"].startswith("collection://"):
            import json
            return {"text": "<data-source-state>\n%s\n</data-source-state>" % json.dumps(STATE)}
        if name == "notion-query-data-sources":
            return {"results": [{"Name": "A", "Status": "Not started", "url": "https://x/p/a"}], "has_more": False}
        return {}


class Client(unittest.TestCase):
    def test_query_translates_filter_sort_and_limit(self):
        fake = FakeMcp()
        cl = mb.McpClient("ds", fake)
        body = nt.build_query({"name": "Open", "filter": {"property": "Status", "status": {"does_not_equal": "Deployed"}}}, "", "Name", "me", 30)
        res = cl.query(body)
        data = fake.calls[-1][1]["data"]
        self.assertEqual(data["mode"], "rows")
        self.assertEqual(data["limit"], 30)
        self.assertEqual(data["filter"]["type"], "group")
        self.assertEqual(nt.page_title(res["results"][0]), "A")
