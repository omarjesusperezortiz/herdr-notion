"""Filter pills (A1), toasts and undo (A2) and the other polish tasks in TASKS-UX.md."""
import copy
import curses
import time
import unittest
from unittest import mock

from herdr_notion import notion as nt
from tests.support import make_app, page

PAGES = [page(0, "Bravo", "To do", "Low", "2026-10-01T00:00:00Z"), page(1, "Alpha", "Done", "High", "2026-10-03T00:00:00Z"),
         page(2, "Charlie", "In progress", "High", "2026-10-02T00:00:00Z"), page(3, "Delta", "To do", "Low", "2026-10-04T00:00:00Z"),
         page(4, "Echo", "Review", "High", "2026-10-05T00:00:00Z")]


def fresh(**kw):
    return make_app(copy.deepcopy(PAGES), **kw)


class Pills(unittest.TestCase):
    def test_no_filter_no_pill_row(self):
        app, scr = fresh(h=28, w=100)
        self.assertEqual(app.filter_pills(), [])
        app.draw_list()
        self.assertFalse(scr.has("Esc removes the last"))
        self.assertTrue(scr.row(2).strip().startswith("TITLE"))

    def test_status_quick_and_search_become_pills_in_the_order_esc_removes_them(self):
        app, scr = fresh(h=28, w=110)
        app.set_status_filter("Review")
        app.quick = "week"
        app.live = "ech"
        app.refilter()
        self.assertEqual([k for k, _ in app.filter_pills()], ["status", "quick", "search"])
        app.draw_list()
        row = scr.row(2)
        for text in ("Review ×", "changed this week ×", "“ech” ×", "Esc removes the last"):
            self.assertIn(text, row)
        self.assertTrue(scr.row(3).strip().startswith("TITLE"))  # the table moved down one row

    def test_esc_removes_one_filter_at_a_time_newest_first_and_then_quits(self):
        app, _ = fresh(h=28, w=110)
        app.set_status_filter("Review")
        app.quick = "week"
        app.refilter()
        app.list_key(27)
        self.assertEqual(app.quick, "")
        self.assertEqual(app.status_filter, "Review")
        app.list_key(27)
        self.assertEqual(app.status_filter, "")
        self.assertIs(app.list_key(27), False)  # nothing left to remove: Esc quits

    def test_a_pill_click_removes_just_that_filter(self):
        app, scr = fresh(h=28, w=110)
        app.set_status_filter("Review")
        app.quick = "today"
        app.refilter()
        app.draw_list()
        x, y = next((h[1] + 1, h[0]) for h in app.hits if h[3] == "pill" and h[4] == "status")
        with mock.patch("curses.getmouse", return_value=(0, x, y, 0, curses.BUTTON1_PRESSED)):
            app.list_key(curses.KEY_MOUSE)
        self.assertEqual(app.status_filter, "")
        self.assertEqual(app.quick, "today")

    def test_rows_and_hits_follow_the_pill_row(self):
        app, scr = fresh(h=28, w=110)
        app.quick = "week"
        app.refilter()
        app.all[0]["last_edited_time"] = "2999-01-01T00:00:00Z"
        app.refilter()
        app.draw_list()
        rows = [h for h in app.hits if h[3] == "row"]
        if rows:
            self.assertEqual(min(h[0] for h in rows), 4)


class Toasts(unittest.TestCase):
    def test_a_status_change_toasts_with_undo_and_the_toast_is_drawn_in_the_footer(self):
        app, scr = fresh(h=28, w=100)
        app.popup = lambda title, items, start=0, at=None: items.index("Review")
        app.confirm = lambda text: True
        app.sel = 0
        app.change_status()
        self.assertEqual(app.toast_state["kind"], "ok")
        self.assertIn("u undo", app.toast_state["text"])
        app.draw_list()
        self.assertTrue(scr.has("✓ Status set to Review · u undo"))

    def test_a_toast_expires(self):
        app, scr = fresh(h=28, w=100)
        app.toast("hello")
        self.assertIsNotNone(app.live_toast())
        with mock.patch("time.time", return_value=time.time() + 60):
            self.assertIsNone(app.live_toast())

    def test_an_error_toast_is_red_and_stays_until_a_key(self):
        app, scr = fresh(h=28, w=100)
        app.colors = True
        app.pairs = {("red", False): 3, ("green", False): 2}
        app.report(nt.NotionError("Notion 429, try again"))
        self.assertEqual(app.toast_state["kind"], "err")
        with mock.patch("curses.color_pair", lambda n: n << 8):
            app.draw_list()
        row = next(y for y in range(24, 28) if "429" in "".join(scr.grid[y]))
        col = "".join(scr.grid[row]).index("✕")
        self.assertEqual(scr.attrs[row][col] >> 8 & 0xff, 3)
        # it outlives the 8 s of an ok toast
        with mock.patch("time.time", return_value=time.time() + 10):
            self.assertIsNotNone(app.live_toast())

    def test_the_run_loop_drops_an_error_toast_at_the_next_key(self):
        from herdr_notion import tui
        app, _ = fresh()
        app.toast("boom", "err")
        # the loop clears an err toast after any key but a mouse event: same condition as in run()
        self.assertTrue(app.toast_state and app.toast_state["kind"] == "err")

    def test_a_job_label_wins_over_a_toast_while_loading(self):
        app, scr = fresh(h=28, w=100)
        app.toast("older message")
        app.job = {"kind": "list", "label": "Loading…", "done": False, "seq": 1, "on_done": None}
        app.draw_list()
        self.assertTrue(scr.has("Loading…"))
        self.assertFalse(scr.has("older message"))


class Undo(unittest.TestCase):
    def status(self, app, i):
        return nt.prop_text(app.pages[i]["properties"]["Status"])

    def test_u_puts_the_old_status_back(self):
        app, _ = fresh()
        app.popup = lambda title, items, start=0, at=None: items.index("Review")
        app.confirm = lambda text: True
        app.sel = 0
        old = self.status(app, 0)
        app.change_status()
        self.assertEqual(self.status(app, 0), "Review")
        app.list_key(ord("u"))
        self.assertEqual(self.status(app, 0), old)
        self.assertIn("undone", app.toast_state["text"])
        self.assertEqual(len(app.client.writes), 2)

    def test_undo_covers_a_bulk_change(self):
        app, _ = fresh()
        app.marked = {app.pages[0]["id"], app.pages[1]["id"]}
        before = {p["id"]: nt.prop_text(p["properties"]["Status"]) for p in app.pages}
        app.popup = lambda title, items, start=0, at=None: items.index("Review")
        app.confirm = lambda text: True
        app.change_status()
        app.undo_last()
        after = {p["id"]: nt.prop_text(p["properties"]["Status"]) for p in app.pages}
        self.assertEqual(before, after)

    def test_undo_after_advance_and_board_move(self):
        app, _ = fresh()
        app.sel = 0
        app.advance_ok = True
        old = self.status(app, 0)
        app.advance_status()
        self.assertNotEqual(self.status(app, 0), old)
        app.undo_last()
        self.assertEqual(self.status(app, 0), old)

    def test_nothing_to_undo_says_so_and_writes_nothing(self):
        app, _ = fresh()
        app.undo_last()
        self.assertEqual(app.toast_state["text"], "nothing to undo")
        self.assertEqual(app.client.writes, [])

    def test_undo_expires_after_a_minute_and_works_only_once(self):
        app, _ = fresh()
        app.popup = lambda title, items, start=0, at=None: items.index("Review")
        app.confirm = lambda text: True
        app.change_status()
        app.undo_last()
        n = len(app.client.writes)
        app.undo_last()
        self.assertEqual(len(app.client.writes), n)
        app.change_status()
        with mock.patch("time.time", return_value=time.time() + 120):
            app.undo_last()
        self.assertEqual(len(app.client.writes), n + 1)  # only the second change; the expired undo wrote nothing

    def test_u_is_listed_in_the_help(self):
        from herdr_notion.tui import HELP
        self.assertTrue(any(k == "u" for _, items in HELP for k, _ in items))


import json
import os
import tempfile


def fresh_in(state_dir, **kw):
    return make_app(copy.deepcopy(PAGES), state_dir=state_dir, **kw)


class ListCache(unittest.TestCase):
    def test_a_fresh_list_is_saved_and_a_new_session_draws_it_at_once(self):
        d = tempfile.mkdtemp()
        app, _ = fresh_in(d)
        with open(os.path.join(d, "list-cache.json")) as f:
            saved = json.load(f)
        key = app.list_cache_key()
        self.assertEqual(len(saved[key]["pages"]), 5)
        app2, scr = fresh_in(d)
        app2.all, app2.pages = [], []
        self.assertTrue(app2.show_cached())
        self.assertEqual(len(app2.pages), 5)
        self.assertIsNotNone(app2.cached_at)
        app2.draw_list()
        self.assertTrue(scr.has("cached"))
        self.assertTrue(scr.has("ago"))

    def test_the_fresh_list_replaces_it_and_the_cached_mark_goes(self):
        d = tempfile.mkdtemp()
        app, _ = fresh_in(d)
        app.show_cached()
        self.assertIsNotNone(app.cached_at)
        app.apply_list(app.client.discover(), {}, app.client.query({}))
        self.assertIsNone(app.cached_at)

    def test_showing_the_cache_does_not_save_it_again(self):
        d = tempfile.mkdtemp()
        app, _ = fresh_in(d)
        path = os.path.join(d, "list-cache.json")
        before = os.path.getmtime(path)
        with open(path) as f:
            at = json.load(f)[app.list_cache_key()]["at"]
        time.sleep(0.01)
        app.show_cached()
        with open(path) as f:
            self.assertEqual(json.load(f)[app.list_cache_key()]["at"], at)

    def test_searches_server_side_status_and_the_off_switch_are_not_cached(self):
        d = tempfile.mkdtemp()
        app, _ = fresh_in(d)
        os.remove(os.path.join(d, "list-cache.json"))
        app.query = "x"
        app.save_list_cache(app.client.discover(), {"results": app.all})
        self.assertFalse(os.path.exists(os.path.join(d, "list-cache.json")))
        app.query, app.server_status = "", True
        app.save_list_cache(app.client.discover(), {"results": app.all})
        self.assertFalse(os.path.exists(os.path.join(d, "list-cache.json")))
        app.server_status = False
        app.cfg = dict(app.cfg, cache=False)
        app.save_list_cache(app.client.discover(), {"results": app.all})
        self.assertFalse(os.path.exists(os.path.join(d, "list-cache.json")))
        self.assertFalse(app.show_cached())

    def test_only_the_latest_tabs_are_kept(self):
        d = tempfile.mkdtemp()
        app, _ = fresh_in(d)
        for i in range(12):
            app.cfg["filters"] = [{"name": "Tab %d" % i}]
            app.fidx = 0
            app.save_list_cache(app.client.discover(), {"results": app.all})
            time.sleep(0.002)
        with open(os.path.join(d, "list-cache.json")) as f:
            self.assertEqual(len(json.load(f)), app.CACHED_TABS)

    def test_switching_tab_shows_that_tabs_saved_list_while_loading(self):
        d = tempfile.mkdtemp()
        app, _ = fresh_in(d)
        app.fidx = 1
        app.save_list_cache(app.client.discover(), {"results": app.all[:2]})
        app.fidx = 0
        app.refresh_async = lambda *a, **k: None
        app.pick_filter(1)
        self.assertEqual(len(app.pages), 2)
        self.assertIsNotNone(app.cached_at)

    def test_an_unreadable_cache_file_is_ignored(self):
        d = tempfile.mkdtemp()
        app, _ = fresh_in(d)
        with open(os.path.join(d, "list-cache.json"), "w") as f:
            f.write("{not json")
        self.assertFalse(app.show_cached())


class RememberedLayout(unittest.TestCase):
    def test_sort_group_compact_and_preview_are_kept_per_tab(self):
        d = tempfile.mkdtemp()
        app, _ = fresh_in(d, h=20, w=150)
        app.list_key(ord("O"))
        app.list_key(ord("g"))
        app.list_key(ord("z"))
        app.list_key(ord("p"))
        app2, _ = fresh_in(d, h=20, w=150)
        app2.apply_layout()
        self.assertEqual((app2.sort_mode, app2.group, app2.compact, app2.preview_on), (1, True, True, False))

    def test_another_tab_has_its_own_layout(self):
        d = tempfile.mkdtemp()
        app, _ = fresh_in(d, h=20, w=150)
        app.list_key(ord("g"))
        app.refresh_async = lambda *a, **k: None
        app.pick_filter(1)
        self.assertFalse(app.group)
        app.list_key(ord("z"))
        app.pick_filter(0)
        self.assertTrue(app.group)
        self.assertFalse(app.compact)

    def test_a_click_sort_is_remembered_too(self):
        d = tempfile.mkdtemp()
        app, _ = fresh_in(d)
        app.click_sort("title")
        app2, _ = fresh_in(d)
        app2.apply_layout()
        self.assertEqual(app2.sort_mode, 1)

    def test_a_new_tab_gets_the_defaults(self):
        d = tempfile.mkdtemp()
        app, _ = fresh_in(d)
        app.apply_layout()
        self.assertEqual((app.sort_mode, app.sort_rev, app.group, app.compact, app.preview_on), (0, False, False, False, True))


class Columns(unittest.TestCase):
    def run_checklist(self, app, keys, items, checked):
        queue = iter(keys)

        class Win:
            def keypad(self, f): pass
            def box(self): pass
            def refresh(self): pass
            def addnstr(self, *a): pass
            def addstr(self, *a): pass
            def getch(self): return next(queue)

        with mock.patch("curses.newwin", lambda *a: Win()):
            return app.checklist("Columns", items, checked)

    def test_space_ticks_and_enter_keeps_the_ticked_ones_in_list_order(self):
        app, _ = fresh()
        # ticked ones come first (B), then the rest (A, C); j moves to A, space ticks it
        got = self.run_checklist(app, [ord("j"), ord(" "), 10], ["A", "B", "C"], ["B"])
        self.assertEqual(got, ["B", "A"])

    def test_space_on_a_ticked_one_unticks_it(self):
        app, _ = fresh()
        self.assertEqual(self.run_checklist(app, [ord(" "), 10], ["A", "B"], ["A"]), [])

    def test_escape_cancels(self):
        app, _ = fresh()
        self.assertIsNone(self.run_checklist(app, [27], ["A", "B"], ["A"]))

    def test_j_and_k_reorder(self):
        app, _ = fresh()
        got = self.run_checklist(app, [ord("J"), 10], ["A", "B"], ["A", "B"])
        self.assertEqual(got, ["B", "A"])

    def test_picking_columns_updates_the_table_the_fetch_and_the_saved_layout(self):
        d = tempfile.mkdtemp()
        app, scr = fresh_in(d, h=20, w=100)
        app.refresh_async = lambda *a, **k: None
        app.checklist = lambda title, items, checked: ["Priority"]
        app.pick_columns()
        self.assertEqual(app.cfg["columns"], ["Priority"])
        self.assertIn("Priority", app.client.want)
        with open(os.path.join(d, "layout.json")) as f:
            self.assertEqual(json.load(f)["_columns"][app.cfg["data_source_id"]], ["Priority"])
        self.assertIn("columns: Priority", app.toast_state["text"])

    def test_saved_columns_are_applied_when_the_config_loads(self):
        d = tempfile.mkdtemp()
        app, _ = fresh_in(d)
        with open(os.path.join(d, "layout.json"), "w") as f:
            json.dump({"_columns": {"ds": ["Priority"]}}, f)
        cfg = {"columns": ["Status", "Priority", "Assignee"], "data_source_id": "ds"}
        saved = app.read_json("layout.json").get("_columns", {}).get(cfg["data_source_id"])
        self.assertEqual(saved, ["Priority"])

    def test_the_picker_never_offers_the_title_or_the_status(self):
        app, _ = fresh()
        seen = {}
        app.refresh_async = lambda *a, **k: None
        app.checklist = lambda title, items, checked: seen.update(items=items) or None
        app.pick_columns()
        self.assertNotIn("Name", seen["items"])
        self.assertNotIn("Status", seen["items"])
        self.assertIn("Priority", seen["items"])


class AgentGlyphs(unittest.TestCase):
    def test_elapsed_text_is_short_and_honest(self):
        e = nt.elapsed_text
        self.assertEqual([e(s) for s in (0, 47, 59, 60, 720, 3599, 3600, 7440, 86399, 86400, 4 * 86400 + 3 * 3600 + 5)],
                         ["0s", "47s", "59s", "1m", "12m", "59m", "1h00m", "2h04m", "23h59m", "1d0h", "4d3h"])
        self.assertEqual(e(-5), "0s")

    def test_agent_state_uses_herdrs_glyphs_and_colors(self):
        s = nt.agent_state
        self.assertEqual(s("working"), ("◐", "amber", "working"))
        self.assertEqual(s("idle"), ("✓", "grey", "idle"))
        self.assertEqual(s("done"), ("●", "green", "done"))
        self.assertEqual(s("idle", done=True), ("●", "green", "done"))
        self.assertEqual(s("blocked")[0], "◉")
        self.assertEqual(s("needs_input")[:2], ("◉", "red"))
        self.assertEqual(s("sent"), ("○", "grey", "sent"))
        self.assertEqual(s(None), ("○", "grey", "sent"))

    def test_the_agent_cell_shows_glyph_name_and_running_time(self):
        app, scr = fresh(h=20, w=110)
        pid = app.pages[0]["id"]
        app.delegations[pid] = {"pane": "p", "agent": "claude", "title": "t", "status": "working", "done": False,
                                "started": time.time() - 12 * 60 - 5}
        self.assertEqual(app.agent_cell(app.pages[0]), ("◐ claude 12m", "amber"))
        app.draw_list()
        self.assertTrue(scr.has("◐ claude 12m"))

    def test_a_finished_agents_time_stops_at_the_end(self):
        app, _ = fresh()
        pid = app.pages[0]["id"]
        now = time.time()
        app.delegations[pid] = {"pane": "p", "agent": "claude", "title": "t", "status": "idle", "done": True,
                                "started": now - 3 * 3600, "ended": now - 3 * 3600 + 125}
        self.assertEqual(app.agent_cell(app.pages[0]), ("● claude 2m", "green"))

    def test_tracking_stamps_the_start_and_the_end_is_stamped_when_it_finishes(self):
        app, _ = fresh()
        app.save_delegations = lambda: None
        app.track(app.pages[0], "w1:p1", "claude")
        d = app.delegations[app.pages[0]["id"]]
        self.assertAlmostEqual(d["started"], time.time(), delta=5)
        app.on_agents([{"pane_id": "w1:p1", "status": "working"}])
        app.on_agents([{"pane_id": "w1:p1", "status": "idle"}])
        self.assertTrue(d["done"])
        self.assertIn("ended", d)

    def test_a_needs_you_agent_is_red(self):
        app, _ = fresh()
        pid = app.pages[0]["id"]
        app.delegations[pid] = {"pane": "p", "agent": "codex", "title": "t", "status": "blocked", "done": False}
        self.assertEqual(app.agent_cell(app.pages[0]), ("◉ codex", "red"))
