"""Colors: the status ramp, Notion's colors, the selection background, role colors (C1 to C4 in TASKS-UX.md)."""
import copy
import curses
import os
import tempfile
import unittest
from unittest import mock

from herdr_notion import config as cfgmod
from herdr_notion import notion as nt
from herdr_notion import palette as pl
from tests.support import SCHEMA, make_app, page

OPTIONS = ["Not started", "Blocked", "In progress", "Review", "To test", "Testing", "To be deployed", "Test in production", "Deployed"]
GROUPS = {"Not started": "To-do", "Blocked": "To-do", "In progress": "In progress", "Review": "In progress", "To test": "In progress",
          "Testing": "In progress", "To be deployed": "In progress", "Test in production": "In progress", "Deployed": "Complete"}


class Ramp(unittest.TestCase):
    def test_nine_statuses_get_nine_steps_along_the_workflow(self):
        c = pl.status_colors(OPTIONS, GROUPS)
        self.assertEqual([c[o] for o in OPTIONS], ["grey", "red", "blue", "purple", "teal", "teal", "amber", "orange", "green"])

    def test_blocked_like_names_are_red_whatever_their_group(self):
        for name in ("Blocked", "On hold", "Waiting for design", "Stuck", "Paused"):
            self.assertEqual(pl.status_colors([name, "Done"], {name: "In progress", "Done": "Complete"})[name], "red")

    def test_an_override_in_the_config_wins_and_a_bad_color_is_ignored(self):
        c = pl.status_colors(OPTIONS, GROUPS, {"Review": "orange", "Testing": "nonsense"})
        self.assertEqual(c["Review"], "orange")
        self.assertEqual(c["Testing"], "teal")

    def test_a_single_status_in_progress_takes_the_first_color(self):
        self.assertEqual(pl.status_colors(["To do", "Doing", "Done"], {"To do": "To-do", "Doing": "In progress", "Done": "Complete"})["Doing"], "blue")

    def test_options_without_groups_fall_back_to_their_names(self):
        c = pl.status_colors(["Backlog", "Doing", "Shipped"], {})
        self.assertEqual((c["Backlog"], c["Doing"], c["Shipped"]), ("grey", "blue", "green"))

    def test_glyphs_fill_up_along_the_workflow(self):
        g = pl.status_glyphs(OPTIONS, GROUPS)
        self.assertEqual([g[o] for o in OPTIONS], ["○", "⊘", "◐", "◐", "◑", "◑", "◕", "◕", "✓"])


class Nearest(unittest.TestCase):
    def test_nearest_256(self):
        self.assertEqual(pl.nearest_256("#000000"), 16)
        self.assertEqual(pl.nearest_256("#ffffff"), 231)
        self.assertEqual(pl.nearest_256("#d75f5f"), 167)
        self.assertIsNone(pl.nearest_256("nope"))

    def test_herdr_selection_background_is_read_from_the_theme_block(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "config.toml")
        with open(path, "w") as f:
            f.write('[ui]\nmouse_capture = true\n\n[theme.custom]\nactive_row_bg = "#414868"\n\n[other]\nactive_row_bg = "#ffffff"\n')
        self.assertEqual(pl.herdr_selection_bg(path), pl.nearest_256("#414868"))
        with open(path, "w") as f:
            f.write("[ui]\nmouse_capture = true\n")
        self.assertIsNone(pl.herdr_selection_bg(path))
        self.assertIsNone(pl.herdr_selection_bg(os.path.join(d, "missing.toml")))

    def test_notion_color_names(self):
        self.assertEqual([pl.notion_color(n) for n in ("red", "yellow", "green", "gray", "pink", "default", None)],
                         ["red", "amber", "green", "grey", "purple", None, None])


class Init(unittest.TestCase):
    def app(self):
        app, _ = make_app([page(0, "A")])
        return app

    def run_init(self, colors, no_color=False):
        app = self.app()
        env = dict(os.environ)
        if no_color:
            env["NO_COLOR"] = "1"
        else:
            env.pop("NO_COLOR", None)
        pairs = []
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("curses.start_color"), mock.patch("curses.use_default_colors"), \
                mock.patch("curses.COLORS", colors, create=True), mock.patch("curses.init_pair", lambda *a: pairs.append(a)), \
                mock.patch("herdr_notion.palette.herdr_selection_bg", return_value=None):
            app.init_colors()
        return app, pairs

    def test_256_colors_give_every_role_a_plain_and_a_selected_pair(self):
        app, pairs = self.run_init(256)
        self.assertTrue(app.colors and app.sel_bg)
        for key in pl.KEYS + ("plain",):
            self.assertIn((key, False), app.pairs)
            self.assertIn((key, True), app.pairs)
        sel = next(p for p in pairs if p[0] == app.pairs[("red", True)])
        self.assertEqual((sel[1], sel[2]), (pl.XTERM["red"], pl.DEFAULT_SELECTION))

    def test_eight_colors_fold_back_and_keep_reverse_video_for_the_selection(self):
        app, pairs = self.run_init(8)
        self.assertTrue(app.colors)
        self.assertFalse(app.sel_bg)
        self.assertEqual(app.on_attr(), curses.A_REVERSE)
        self.assertTrue(all(p[1] < 8 for p in pairs))

    def test_no_color_means_no_pairs_and_grey_is_dim(self):
        app, pairs = self.run_init(256, no_color=True)
        self.assertFalse(app.colors)
        self.assertEqual(pairs, [])
        self.assertEqual(app.paint("grey"), curses.A_DIM)
        self.assertEqual(app.paint("red"), 0)


class Screens(unittest.TestCase):
    def colored(self, **kw):
        pages = [page(0, "Bravo", "To do", "Low"), page(1, "Alpha", "Done", "High"), page(2, "Charlie", "In progress", "High")]
        app, scr = make_app(pages, **kw)
        app.colors, app.sel_bg = True, True
        keys = pl.KEYS + ("plain", "tab")
        app.pairs = {}
        for n, key in enumerate(keys):
            app.pairs[(key, False)] = 2 * n + 1
            app.pairs[(key, True)] = 2 * n + 2
        return app, scr

    def draw(self, app):
        with mock.patch("curses.color_pair", lambda n: n << 8):
            app.draw_list()

    def test_the_status_bar_and_word_use_the_status_color(self):
        app, scr = self.colored(h=28, w=100)
        self.draw(app)
        row = next(y for y in range(3, 10) if "Alpha" in "".join(scr.grid[y]))
        done = app.pairs[("green", False)]
        self.assertEqual(scr.attrs[row][0] >> 8 & 0xff, done)
        col = "".join(scr.grid[row]).index("✓")
        self.assertEqual(scr.attrs[row][col] >> 8 & 0xff, done)

    def test_the_selected_row_keeps_its_colors_on_the_selection_background_and_is_not_reversed(self):
        app, scr = self.colored(h=28, w=100)
        app.sel = 0
        self.draw(app)
        row = next(y for y in range(3, 10) if "Bravo" in "".join(scr.grid[y]))
        self.assertFalse(any(a & curses.A_REVERSE for a in scr.attrs[row][:60]))
        self.assertEqual(scr.attrs[row][0] >> 8 & 0xff, app.pairs[("grey", True)])  # To do: grey bar on the selection color
        self.assertEqual(scr.attrs[row][60] >> 8 & 0xff, app.pairs[("plain", True)])  # the blank part of the row

    def test_without_256_colors_the_selected_row_is_reverse_video(self):
        app, scr = self.colored(h=28, w=100)
        app.sel_bg = False
        app.sel = 0
        self.draw(app)
        row = next(y for y in range(3, 10) if "Bravo" in "".join(scr.grid[y]))
        self.assertTrue(scr.attrs[row][10] & curses.A_REVERSE)

    def test_the_priority_column_marks_high_and_colors_it_red(self):
        app, scr = self.colored(h=28, w=100)
        self.draw(app)
        row = next(y for y in range(3, 10) if "Alpha" in "".join(scr.grid[y]))
        line = "".join(scr.grid[row])
        self.assertIn("!! High", line)
        col = line.index("!! High")
        self.assertEqual(scr.attrs[row][col] >> 8 & 0xff, app.pairs[("red", False)])
        self.assertTrue(scr.attrs[row][col] & curses.A_BOLD)

    def test_chips_and_group_headers_use_the_status_glyph_and_color(self):
        app, scr = self.colored(h=28, w=120)
        app.list_key(ord("g"))
        self.draw(app)
        self.assertTrue(scr.has("◕ Review") or scr.has("◐ In progress"))
        self.assertTrue(scr.has("✓ Done"))


class CellStyle(unittest.TestCase):
    def setUp(self):
        self.app, _ = make_app([page(0, "A")])
        schema = copy.deepcopy(SCHEMA)
        schema["properties"]["Priority"]["select"]["options"] = [
            {"id": "High", "name": "High", "color": "red"}, {"id": "Low", "name": "Low", "color": "green"},
            {"id": "Mid", "name": "Mid", "color": "yellow"}]
        schema["properties"]["Area"] = {"type": "select", "select": {"options": [{"id": "Web", "name": "Web", "color": "blue"}]}}
        self.app.option_colors = nt.select_colors(schema)

    def test_select_colors_reads_the_options_that_have_one(self):
        self.assertEqual(self.app.option_colors["Priority"], {"High": "red", "Low": "green", "Mid": "yellow"})
        self.assertEqual(self.app.option_colors["Area"], {"Web": "blue"})

    def test_notion_colors_win_and_priority_columns_get_marks(self):
        self.assertEqual(self.app.cell_style("Priority", "High"), ("!! High", "red", True))
        self.assertEqual(self.app.cell_style("Priority", "Mid"), ("!  Mid", "amber", False))
        self.assertEqual(self.app.cell_style("Priority", "Low"), ("Low", "green", False))
        self.assertEqual(self.app.cell_style("Area", "Web"), ("Web", "blue", False))

    def test_without_notion_colors_urgent_words_are_still_red(self):
        self.app.option_colors = {}
        self.assertEqual(self.app.cell_style("Priority", "Urgent"), ("!! Urgent", "red", True))
        self.assertEqual(self.app.cell_style("Owner", "high"), ("high", "red", True))
        self.assertEqual(self.app.cell_style("Owner", "Ada"), ("Ada", None, False))


class Config(unittest.TestCase):
    def test_colors_table_and_theme_are_read(self):
        n = cfgmod.normalize({"notion": {"data_source_id": "x", "theme": "mono"}, "colors": {"Review": "orange"}})
        self.assertEqual(n["colors"], {"Review": "orange"})
        self.assertEqual(n["theme"], "mono")
        n = cfgmod.normalize({"notion": {"data_source_id": "x"}})
        self.assertEqual((n["colors"], n["theme"]), ({}, "default"))

    def test_the_ramp_follows_the_config_override(self):
        app, _ = make_app([page(0, "A")])
        app.cfg = dict(app.cfg, colors={"Review": "orange"})
        app.apply_list(SCHEMA, {}, app.client.query({}))
        self.assertEqual(app.status_key("Review"), "orange")
        self.assertEqual(app.status_key("Done"), "green")
