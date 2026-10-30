import unittest

from herdr_notion import config, setup, tomlcompat

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
    def test_status_candidates_prefers_status_type(self):
        self.assertEqual(setup.status_candidates(SCHEMA), ["State", "Priority"])

    def test_column_candidates(self):
        self.assertEqual(setup.column_candidates(SCHEMA, "State"), ["Owner", "Priority"])

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
        self.assertEqual(cfg["columns"], ["State", "Owner", "Priority"])
        self.assertEqual([f["name"] for f in cfg["filters"]], ["Mine", "Open", "All"])
        self.assertEqual(cfg["filters"][1]["filter"]["and"][0]["status"]["does_not_equal"], "Done")
        self.assertEqual(cfg["filters"][0]["filter"]["and"][0], {"property": "Owner", "people": {"contains": "me"}})

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
        self.assertEqual([f["name"] for f in cfg["filters"]], ["Mine", "All"])
        self.assertEqual(cfg["filters"][0]["filter"], {"property": "Owner", "people": {"contains": "me"}})
        self.assertEqual(cfg["status_property"], "")


if __name__ == "__main__":
    unittest.main()


class SuggestedFilters(unittest.TestCase):
    def test_sprint_checkbox_and_formula(self):
        box = {"properties": {"This sprint": {"type": "checkbox", "checkbox": {}}}}
        self.assertEqual(setup.sprint_filter(box), {"property": "This sprint", "checkbox": {"equals": True}})
        formula = {"properties": {"Current sprint?": {"type": "formula", "formula": {}}}}
        self.assertEqual(setup.sprint_filter(formula), {"property": "Current sprint?", "formula": {"checkbox": {"equals": True}}})
        self.assertIsNone(setup.sprint_filter({"properties": {"Sprints": {"type": "relation", "relation": {}}}}))

    def test_person_property_prefers_assignee_names(self):
        schema = {"properties": {"Created by me": {"type": "people", "people": {}}, "Assignee": {"type": "people", "people": {}}}}
        self.assertEqual(setup.person_property(schema), "Assignee")

    def test_mine_is_first_and_excludes_finished(self):
        flt = setup.suggest_filters(SCHEMA, "State")
        self.assertEqual([f["name"] for f in flt], ["Mine", "Open", "All"])
        self.assertEqual(len(flt[0]["filter"]["and"]), 2)

    def test_sprint_tabs_only_when_the_database_has_one(self):
        schema = dict(SCHEMA, properties=dict(SCHEMA["properties"], **{"Current sprint?": {"type": "formula", "formula": {}}}))
        names = [f["name"] for f in setup.suggest_filters(schema, "State")]
        self.assertEqual(names, ["Mine", "Sprint", "Open", "All"])  # four tabs: the sprint tab is yours when we know who you are

    def test_plain_database_still_gets_all(self):
        self.assertEqual(setup.suggest_filters({"properties": {"Name": {"type": "title", "title": {}}}}, ""), [{"name": "All"}])


class ReplaceFilters(unittest.TestCase):
    def test_keeps_everything_else(self):
        text = '[notion]\ndata_source_id = "d"\n\n[[filters]]\nname = "All"\n\n[delegate]\nprompt = "p"\n\n[[filters]]\nname = "Old"\n'
        out = setup.replace_filters(text, [{"name": "Mine", "filter": {"property": "Owner", "people": {"contains": "me"}}}, {"name": "All"}])
        cfg = config.normalize(tomlcompat.loads(out))
        self.assertEqual([f["name"] for f in cfg["filters"]], ["Mine", "All"])
        self.assertEqual(cfg["delegate"]["prompt"], "p")
        self.assertEqual(cfg["data_source_id"], "d")


class Columns(unittest.TestCase):
    def test_assignee_relation_beats_system_columns(self):
        schema = {"properties": {"Name": {"type": "title", "title": {}}, "Status": {"type": "status", "status": {"options": []}},
                                 "Completed At": {"type": "date", "date": {}}, "Assignee Notion user": {"type": "people", "people": {}},
                                 "Assignee": {"type": "relation", "relation": {}}, "Priority": {"type": "select", "select": {"options": []}}}}
        self.assertEqual(setup.column_candidates(schema, "Status"), ["Assignee", "Priority"])


class MineAndViews(unittest.TestCase):
    def test_mine_uses_the_assignee_relation_when_the_persons_page_is_known(self):
        schema = {"properties": {"Name": {"type": "title", "title": {}}, "Status": {"type": "status", "status": {"options": [], "groups": []}},
                                 "Assignee": {"type": "relation", "relation": {"data_source": "x"}},
                                 "Notion user": {"type": "people", "people": {}}}}
        flt = setup.suggest_filters(schema, "Status", [], ("Assignee", "https://x/p/me"))
        self.assertEqual(flt[0]["name"], "Mine")
        self.assertEqual(flt[0]["filter"], {"property": "Assignee", "relation": {"contains": "https://x/p/me"}})

    def test_views_are_not_extra_tabs(self):
        flt = setup.suggest_filters({"properties": {}}, "", [{"name": "Board", "url": "https://x?v=1"}])
        self.assertEqual([f["name"] for f in flt], ["All"])  # they are one key away (V)

    def test_a_tab_can_still_be_a_saved_view_in_the_config(self):
        text = setup.render_filters([{"name": "All"}, {"name": "▦ Board", "view": "https://x?v=1"}])
        cfg = config.normalize(tomlcompat.loads('[notion]\ndata_source_id = "d"\n\n' + text))
        self.assertEqual(cfg["filters"][1]["view"], "https://x?v=1")