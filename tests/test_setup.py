import unittest

from herdr_notion import config, setup, tomlcompat

SEARCH = {"results": [
    {"object": "data_source", "id": "ds1", "title": [{"plain_text": "Tasks"}], "parent": {"type": "database_id", "database_id": "db1"}},
    {"object": "page", "id": "pg"},
    {"object": "data_source", "id": "ds2", "title": [], "parent": {"database_id": "db2"}},
]}

SCHEMA = {"properties": {
    "Name": {"type": "title", "title": {}},
    "Notes": {"type": "rich_text", "rich_text": {}},
    "Priority": {"type": "select", "select": {"options": []}},
    "State": {"type": "status", "status": {
        "options": [{"id": "a", "name": "Not started"}, {"id": "b", "name": "Done"}, {"id": "c", "name": "Shipped"}],
        "groups": [{"name": "To-do", "option_ids": ["a"]}, {"name": "Complete", "option_ids": ["b", "c"]}]}},
    "Owner": {"type": "people", "people": {}},
    "Due": {"type": "date", "date": {}},
    "Tags": {"type": "multi_select", "multi_select": {"options": []}},
}}


class Discovery(unittest.TestCase):
    def test_database_choices(self):
        self.assertEqual(setup.database_choices(SEARCH), [
            {"id": "ds1", "database_id": "db1", "title": "Tasks"},
            {"id": "ds2", "database_id": "db2", "title": "(untitled)"}])

    def test_status_candidates_prefers_status_type(self):
        self.assertEqual(setup.status_candidates(SCHEMA), ["State", "Priority"])

    def test_column_candidates(self):
        self.assertEqual(setup.column_candidates(SCHEMA, "State"), ["Priority", "Owner"])

    def test_open_filter_excludes_every_complete_option(self):
        self.assertEqual(setup.open_filter(SCHEMA, "State"), {"and": [
            {"property": "State", "status": {"does_not_equal": "Done"}},
            {"property": "State", "status": {"does_not_equal": "Shipped"}}]})

    def test_open_filter_for_plain_select_guesses_finished_options(self):
        schema = {"properties": {"Status": {"type": "select", "select": {"options": [
            {"name": "To Do"}, {"name": "In Progress"}, {"name": "Finished"}]}}}}
        self.assertEqual(setup.open_filter(schema, "Status"),
                         {"property": "Status", "select": {"does_not_equal": "Finished"}})

    def test_open_filter_none_for_select_without_finished_or_missing(self):
        self.assertIsNone(setup.open_filter(SCHEMA, "Priority"))
        self.assertIsNone(setup.open_filter(SCHEMA, "Nope"))


class StatusRanking(unittest.TestCase):
    """Shape of a real task database: several select properties, 'Status' is a select."""
    REAL = {"properties": {n: {"type": t, t: {"options": [{"name": "a"}, {"name": "b"}]}} for n, t in [
        ("Effort", "select"), ("PR Status", "select"), ("Status", "select"), ("Type", "select"),
        ("Priority", "select"), ("Repo", "select")]}}

    def test_property_named_status_wins_over_schema_order(self):
        self.assertEqual(setup.status_candidates(self.REAL)[0], "Status")
        self.assertEqual(setup.status_candidates(self.REAL)[1], "PR Status")

    def test_notion_status_type_beats_other_selects(self):
        schema = {"properties": {"Kind": {"type": "select", "select": {}}, "Phase": {"type": "status", "status": {}}}}
        self.assertEqual(setup.status_candidates(schema), ["Phase", "Kind"])

    def test_named_select_beats_status_type(self):
        schema = {"properties": {"Phase": {"type": "status", "status": {}}, "Status": {"type": "select", "select": {}}}}
        self.assertEqual(setup.status_candidates(schema)[0], "Status")

    def test_label_shows_type_and_options(self):
        label = setup.property_label(self.REAL, "Status")
        self.assertIn("select", label)
        self.assertIn("a, b", label)


class RenderConfig(unittest.TestCase):
    CHOICE = {"id": "ds1", "database_id": "db1", "title": 'Tasks "A"\nline'}

    def test_roundtrips_through_the_loader(self):
        text = setup.render_config(self.CHOICE, SCHEMA, "State")
        cfg = config.normalize(tomlcompat.loads(text))
        self.assertEqual(cfg["database_id"], "db1")
        self.assertEqual(cfg["data_source_id"], "ds1")
        self.assertEqual(cfg["status_property"], "State")
        self.assertEqual(cfg["columns"], ["State", "Priority", "Owner"])
        self.assertEqual([f["name"] for f in cfg["filters"]], ["All", "Open"])
        self.assertEqual(cfg["filters"][1]["filter"]["and"][0]["status"]["does_not_equal"], "Done")

    def test_fallback_parser_agrees(self):
        text = setup.render_config(self.CHOICE, SCHEMA, "State")
        mine = tomlcompat._Parser(text).parse()
        try:
            import tomllib
        except ImportError:
            self.skipTest("no tomllib")
        self.assertEqual(mine, tomllib.loads(text))

    def test_no_status_property(self):
        cfg = config.normalize(tomlcompat.loads(setup.render_config(self.CHOICE, SCHEMA, "")))
        self.assertEqual(cfg["filters"], [{"name": "All"}])
        self.assertEqual(cfg["status_property"], "")


if __name__ == "__main__":
    unittest.main()
