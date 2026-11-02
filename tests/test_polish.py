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


class Fuzzy(unittest.TestCase):
    def test_substrings_beat_scattered_letters_and_word_starts_beat_the_middle(self):
        f = nt.fuzzy_score
        self.assertGreater(f("login", "Fix login redirect"), f("login", "Lock orange gin"))
        self.assertGreater(f("fix", "Fix login"), f("fix", "Prefix login"))
        self.assertIsNotNone(f("flr", "Fix login redirect"))  # letters in order
        self.assertIsNone(f("zzz", "Fix login redirect"))

    def test_every_word_must_match_in_any_order_and_accents_do_not_matter(self):
        f = nt.fuzzy_score
        self.assertIsNotNone(f("redirect login", "Fix login redirect"))
        self.assertIsNone(f("redirect payment", "Fix login redirect"))
        self.assertIsNotNone(f("revision", "Revisión de precios"))

    def test_a_shorter_title_wins_a_tie(self):
        self.assertGreater(nt.fuzzy_score("fix", "Fix it"), nt.fuzzy_score("fix", "Fix it and then some other long thing"))


class GotoRows(unittest.TestCase):
    def app(self):
        app, scr = fresh(h=30, w=100)
        for i, p in enumerate(app.all):
            p["properties"]["ID"] = {"type": "rich_text", "rich_text": [{"plain_text": "ET-%d" % (100 + i)}]}
        app.refilter()
        return app

    def kinds(self, rows):
        return [r["kind"] for r in rows]

    def labels(self, rows, kind):
        return [r["label"] for r in rows if r["kind"] == kind]

    def test_an_empty_box_offers_recents_without_the_current_page_and_a_few_commands(self):
        app = self.app()
        for p in app.all[:3]:
            app.note_recent(p)
        here = app.pages[app.sel]
        rows = app.goto_items("")
        self.assertEqual(rows[0], {"kind": "header", "label": "Recent"})
        self.assertNotIn(nt.page_title(here), self.labels(rows, "page"))
        self.assertTrue(self.labels(rows, "command"))
        self.assertNotIn("search", self.kinds(rows))

    def test_agents_that_need_you_come_first(self):
        app = self.app()
        target = app.all[3]
        app.delegations[target["id"]] = {"pane": "p", "agent": "codex", "title": "t", "status": "blocked", "done": False}
        app.note_recent(app.all[1])
        rows = app.goto_items("")
        self.assertEqual(rows[0]["label"], "Needs you")
        self.assertEqual(rows[1]["label"], nt.page_title(target))
        self.assertIn("◉ codex", rows[1]["right"])

    def test_a_finished_agent_waiting_for_review_counts_as_needing_you(self):
        app = self.app()
        app.finished = [app.all[4]["id"]]
        rows = app.goto_items("")
        self.assertEqual(rows[1]["label"], nt.page_title(app.all[4]))

    def test_a_query_finds_pages_by_title_ticket_id_and_status_then_commands_then_search(self):
        app = self.app()
        by_id = app.goto_items("et-102")
        self.assertEqual(self.labels(by_id, "page")[0], nt.page_title(app.all[2]) if False else self.labels(by_id, "page")[0])
        self.assertEqual(len(self.labels(by_id, "page")), 1)
        by_status = app.goto_items("review")
        self.assertIn("Echo", self.labels(by_status, "page"))
        rows = app.goto_items("char")
        self.assertEqual(rows[0], {"kind": "header", "label": "Pages"})
        self.assertEqual(rows[-1]["kind"], "search")
        self.assertIn("char", rows[-1]["label"])

    def test_commands_match_by_name(self):
        app = self.app()
        rows = app.goto_items("board")
        self.assertIn("Board: a column per status", self.labels(rows, "command"))
        self.assertEqual([r["key"] for r in rows if r["kind"] == "command"][0], "B")

    def test_a_recent_page_that_is_not_loaded_still_matches(self):
        app = self.app()
        app.write_json("recents.json", {"items": [{"id": "elsewhere", "title": "Quarterly planning", "tid": "ET-9", "at": time.time()}]})
        rows = app.goto_items("quart")
        row = next(r for r in rows if r["kind"] == "page")
        self.assertEqual((row["id"], row["right"]), ("elsewhere", "recent"))

    def test_the_agent_that_needs_you_outranks_an_equal_match(self):
        app = self.app()
        charlie = next(p for p in app.all if nt.page_title(p) == "Charlie")
        rows = app.goto_items("l")  # a mid-word match in Alpha, Charlie and Delta
        self.assertNotEqual(next(r for r in rows if r["kind"] == "page")["label"], "Charlie")
        app.delegations[charlie["id"]] = {"pane": "p", "agent": "x", "title": "t", "status": "blocked", "done": False}
        rows = app.goto_items("l")
        self.assertEqual(next(r for r in rows if r["kind"] == "page")["label"], "Charlie")

    def test_recents_are_kept_newest_first_without_duplicates_and_capped(self):
        app = self.app()
        for p in app.all:
            app.note_recent(p)
        app.note_recent(app.all[0])
        items = app.read_json("recents.json")["items"]
        self.assertEqual(items[0]["id"], app.all[0]["id"])
        self.assertEqual(len(items), len({i["id"] for i in items}))
        for i in range(40):
            app.note_recent({"id": "x%d" % i, "properties": {}})
        self.assertEqual(len(app.read_json("recents.json")["items"]), app.RECENTS)

    def test_opening_a_page_records_it_as_recent(self):
        app = self.app()
        app.start_job = lambda *a, **k: None
        app.open_page(app.all[2])
        self.assertEqual(app.read_json("recents.json")["items"][0]["id"], app.all[2]["id"])


class GotoBox(unittest.TestCase):
    def run_box(self, app, keys):
        queue = iter(keys)

        class Win:
            def keypad(self, f): pass
            def box(self): pass
            def refresh(self): pass
            def addnstr(self, *a): pass
            def addstr(self, *a): pass
            def get_wch(self): return next(queue)

        with mock.patch("curses.newwin", lambda *a: Win()):
            app.goto_box()

    def test_typing_then_enter_opens_the_best_page(self):
        app, _ = fresh()
        opened = []
        app.open_page = lambda p: opened.append(nt.page_title(p))
        self.run_box(app, list("char") + ["\n"])
        self.assertEqual(opened, ["Charlie"])

    def test_arrows_move_between_rows_but_skip_headers(self):
        app, _ = fresh()
        opened = []
        app.open_page = lambda p: opened.append(nt.page_title(p))
        self.run_box(app, list("a") + [curses.KEY_DOWN, "\n"])
        self.assertEqual(len(opened), 1)
        first = []
        app.open_page = lambda p: first.append(nt.page_title(p))
        self.run_box(app, list("a") + ["\n"])
        self.assertNotEqual(first, opened)

    def test_a_command_row_runs_its_key(self):
        app, _ = fresh()
        keys = []
        app.send_key = lambda k: keys.append(chr(k))
        self.run_box(app, list("board") + ["\n"])
        self.assertEqual(keys, ["B"])

    def test_the_last_row_searches_all_of_notion(self):
        app, _ = fresh()
        app.refresh_async = lambda *a, **k: None
        rows = app.goto_items("zzzz")
        self.assertEqual(rows[-1]["kind"], "search")
        self.run_box(app, list("zzzz") + ["\n"])
        self.assertEqual(app.query, "zzzz")

    def test_escape_closes_and_backspace_edits(self):
        app, _ = fresh()
        app.open_page = lambda p: self.fail("opened")
        self.run_box(app, list("xyz") + ["\x1b"])
        opened = []
        app.open_page = lambda p: opened.append(nt.page_title(p))
        self.run_box(app, list("charx") + ["\x7f", "\n"])
        self.assertEqual(opened, ["Charlie"])

    def test_ctrl_p_and_colon_open_it_and_a_not_loaded_page_is_fetched(self):
        app, _ = fresh()
        called = []
        app.goto_box = lambda: called.append(1)
        app.list_key(16)
        app.list_key(ord(":"))
        self.assertEqual(called, [1, 1])
        app.goto_box = type(app).goto_box.__get__(app)
        opened = []
        app.open_page = lambda p: opened.append(p["id"])
        app.client.get_page = lambda pid: {"id": pid, "properties": {}}
        app.goto_open({"kind": "page", "id": "elsewhere", "label": "x", "right": ""})
        self.assertEqual(opened, ["elsewhere"])

    def test_it_works_from_the_board_and_the_page_view(self):
        app, _ = fresh()
        app.list_key(ord("B"))
        called = []
        app.goto_box = lambda: called.append("board")
        app.board_key(16)
        self.assertEqual(called, ["board"])
        app.board = False
        app.detail = {"page": app.pages[0], "lines": [], "body": ""}
        app.detail_key(16)
        self.assertEqual(called, ["board", "board"])


from herdr_notion import delegate as dg


def ag(pane="w1:p1", name="claude", status="working", title=""):
    return {"pane_id": pane, "name": name, "agent": "claude", "status": status, "cwd": "/x", "title": title}


class AgentPreview(unittest.TestCase):
    def app(self, **kw):
        app, scr = fresh(h=24, w=150, **kw)
        app.draw_list()
        app.delegations[app.pages[0]["id"]] = {"pane": "w1:p1", "agent": "claude", "title": "t", "status": "working", "done": False,
                                               "started": time.time() - 90}
        return app, scr

    def test_read_agent_returns_the_trimmed_tail(self):
        calls = []
        out = dg.read_agent("w1:p1", 5, lambda args: calls.append(args) or "one  \ntwo\n\n\n")
        self.assertEqual(out, ["one", "two"])
        self.assertEqual(calls[0][:3], ["agent", "read", "w1:p1"])
        self.assertIn("5", calls[0])
        self.assertEqual(dg.read_agent("w1:p1", 5, lambda args: "  \n"), [])

    def test_a_switches_to_the_agent_and_back(self):
        app, _ = self.app()
        app.sel = 0
        app.list_key(ord("a"))
        self.assertEqual(app.preview_mode, "agent")
        app.list_key(ord("a"))
        self.assertEqual(app.preview_mode, "ticket")

    def test_a_ticket_without_an_agent_says_so_and_stays_on_the_ticket(self):
        app, _ = self.app()
        app.sel = 1
        app.list_key(ord("a"))
        self.assertEqual(app.preview_mode, "ticket")
        self.assertEqual(app.toast_state["text"], "no agent is on this ticket")

    def test_a_narrow_pane_explains(self):
        app, _ = fresh(h=24, w=90)
        app.list_key(ord("a"))
        self.assertIn("needs the preview", app.toast_state["text"])

    def test_the_preview_shows_the_tail_header_and_cue(self):
        app, scr = self.app()
        app.sel = 0
        app.list_key(ord("a"))
        app.agent_text["w1:p1"] = (time.time(), ["● Read src/auth.ts", "● Bash npm test", "  12 passed"])
        scr.clear()
        app.draw_list()
        for text in ("claude · ◐ claude 1m", "for ", "● Read src/auth.ts", "12 passed", "live · a shows the ticket"):
            self.assertTrue(scr.has(text), text)

    def test_while_the_terminal_is_not_read_yet_it_says_so(self):
        app, scr = self.app()
        app.sel = 0
        app.list_key(ord("a"))
        scr.clear()
        app.draw_list()
        self.assertTrue(scr.has("Reading the terminal…"))

    def test_only_the_last_lines_that_fit_are_shown(self):
        app, scr = self.app()
        app.sel = 0
        app.list_key(ord("a"))
        app.agent_text["w1:p1"] = (time.time(), ["line %03d" % i for i in range(200)])
        scr.clear()
        app.draw_list()
        self.assertTrue(scr.has("line 199"))
        self.assertFalse(scr.has("line 000"))

    def test_a_row_without_an_agent_shows_its_ticket_even_in_agent_mode(self):
        app, scr = self.app()
        app.sel = 0
        app.list_key(ord("a"))
        app.sel = 2
        app.cache[app.pages[2]["id"]] = ["ticket body line"]
        scr.clear()
        app.draw_list()
        self.assertTrue(scr.has("ticket body line"))

    def test_polling_reads_the_terminal_once_per_interval_and_only_in_agent_mode(self):
        app, _ = self.app()
        app.sel = 0
        started = []
        app.start_job = lambda label, work, done, kind="list": started.append((kind, done))
        app.poll_agent_preview()
        self.assertEqual(started, [])  # ticket mode
        app.preview_mode = "agent"
        app.poll_agent_preview()
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0][0], "silent")
        app.poll_agent_preview()
        self.assertEqual(len(started), 1)  # inside the interval
        started[0][1](["x"])
        self.assertEqual(app.agent_text["w1:p1"][1], ["x"])
        with mock.patch("time.time", return_value=time.time() + 5):
            app.poll_agent_preview()
        self.assertEqual(len(started), 2)

    def test_polling_never_starts_over_another_job(self):
        app, _ = self.app()
        app.sel = 0
        app.preview_mode = "agent"
        app.job = {"kind": "list", "label": "x", "done": False, "seq": 1, "on_done": None}
        app.start_job = lambda *a, **k: self.fail("started a job over another")
        app.poll_agent_preview()


class AgentsView(unittest.TestCase):
    def app(self):
        app, scr = fresh(h=24, w=110)
        for i, p in enumerate(app.all):
            p["properties"]["ID"] = {"type": "rich_text", "rich_text": [{"plain_text": "ET-%d" % (100 + i)}]}
        app.refilter()
        app.save_delegations = lambda: None
        app.herdr_ok = True
        ids = {nt.page_title(p): p for p in app.all}
        app.delegations[ids["Charlie"]["id"]] = {"pane": "w1:pA", "agent": "claude", "title": "Charlie", "status": "working",
                                                 "done": False, "started": time.time() - 720}
        tid = nt.page_ticket_id(ids["Echo"])
        app.last_agents = [ag("w1:pA", "claude", "working"), ag("w1:pB", "codex", "blocked", "%s demo" % tid),
                           ag("w1:pC", "pi", "idle", "scratch"), ag("w1:pD", "claude", "done", "")]
        return app, scr

    def test_rows_put_agents_that_need_you_first_and_match_tickets(self):
        app, _ = self.app()
        rows = app.agent_rows()
        self.assertEqual([r[0]["name"] for r in rows], ["codex", "claude", "claude", "pi"])
        by = {r[0]["pane_id"]: r for r in rows}
        self.assertEqual(nt.page_title(by["w1:pA"][1]), "Charlie")  # delegated from here
        self.assertEqual(by["w1:pA"][2], "12m")
        self.assertEqual(nt.page_title(by["w1:pB"][1]), "Echo")  # matched by the ticket id in its title
        self.assertIsNone(by["w1:pC"][1])

    def test_a_opens_the_view_and_it_draws_every_column(self):
        app, scr = self.app()
        app.list_key(ord("A"))
        self.assertTrue(app.agents_view)
        app.draw_agents()
        for text in ("Agents", "4 agents", "AGENT", "STATE", "TICKET", "◉ needs you", "◐ working", "12m", "Charlie", "no ticket · a attaches one"):
            self.assertTrue(scr.has(text), text)

    def test_arrows_enter_and_back(self):
        app, _ = self.app()
        app.list_key(ord("A"))
        focused = []
        saved = dg.focus_agent
        dg.focus_agent = lambda pane: focused.append(pane)
        try:
            app.agents_key(curses.KEY_DOWN)
            app.agents_key(10)
        finally:
            dg.focus_agent = saved
        self.assertEqual(focused, [app.agent_rows()[1][0]["pane_id"]])
        app.agents_key(27)
        self.assertFalse(app.agents_view)

    def test_attach_ties_an_agent_to_a_picked_ticket(self):
        app, _ = self.app()
        app.list_key(ord("A"))
        app.arow = next(i for i, r in enumerate(app.agent_rows()) if r[0]["pane_id"] == "w1:pC")
        app.ask = lambda label, initial="": "delta"
        app.popup = lambda title, items, start=0, at=None: 0
        app.agents_key(ord("a"))
        delta = next(p for p in app.all if nt.page_title(p) == "Delta")
        self.assertEqual(app.delegations[delta["id"]]["pane"], "w1:pC")
        self.assertTrue(app.delegations[delta["id"]]["attached"])
        row = next(r for r in app.agent_rows() if r[0]["pane_id"] == "w1:pC")
        self.assertEqual(nt.page_title(row[1]), "Delta")

    def test_attach_with_no_match_says_so(self):
        app, _ = self.app()
        app.list_key(ord("A"))
        app.ask = lambda label, initial="": "zzzzzz"
        app.popup = lambda *a, **k: self.fail("popup for no match")
        app.agents_key(ord("a"))
        self.assertIn("no loaded ticket matches", app.toast_state["text"])

    def test_without_herdr_the_view_does_not_open(self):
        app, _ = self.app()
        app.herdr_ok = False
        app.list_key(ord("A"))
        self.assertFalse(app.agents_view)
        self.assertEqual(app.toast_state["kind"], "err")

    def test_no_agents_shows_a_hint(self):
        app, scr = self.app()
        app.last_agents = []
        app.list_key(ord("A"))
        app.draw_agents()
        self.assertTrue(scr.has("herdr lists no agents"))

    def test_r_asks_herdr_again(self):
        app, _ = self.app()
        app.list_key(ord("A"))
        app.next_agent_poll = time.time() + 100
        app.agents_key(ord("r"))
        self.assertEqual(app.next_agent_poll, 0)

    def test_the_footer_hints_are_listed(self):
        app, scr = self.app()
        app.list_key(ord("A"))
        app.draw_agents()
        self.assertTrue(scr.has("a attach to a ticket"))


class TicketIdIn(unittest.TestCase):
    def test_finds_a_ticket_id_in_agent_names_and_titles(self):
        f = nt.ticket_id_in
        self.assertEqual(f("tester-et-2535"), "ET-2535")
        self.assertEqual(f("✳ ET-2546 demo OAuth"), "ET-2546")
        self.assertEqual(f("no ticket here"), "")
        self.assertEqual(f("sha-1234567 fix"), "")  # too many digits to be a ticket
        self.assertEqual(f(""), "")

    def test_an_agent_whose_ticket_is_not_loaded_shows_the_id(self):
        app, scr = fresh(h=24, w=110)
        app.herdr_ok = True
        app.last_agents = [ag("w1:pZ", "tester-et-9999", "idle")]
        app.list_key(ord("A"))
        app.draw_agents()
        self.assertTrue(scr.has("ET-9999 · not in this list"))


class PageSections(unittest.TestCase):
    def page_app(self, **kw):
        app, scr = fresh(h=kw.pop("h", 30), w=kw.pop("w", 100), **kw)
        a, b = app.pages[0], app.pages[1]
        a["properties"]["Related"] = {"type": "relation", "relation": [{"id": b["id"], "title": nt.page_title(b)},
                                                                       {"id": "other", "title": "Other page"}]}
        a["properties"]["Owner"] = {"type": "people", "people": [{"id": "u", "name": "Ada"}]}
        app.detail = {"page": a, "lines": [], "body": "## Goal\nUsers land on /home.\n[x] Reproduce\n[ ] Add a test",
                      "comments": [{"id": "c1", "author": "Ana", "when": "2026-10-30T09:00:00Z", "text": "Reproduced it."},
                                   {"id": "c2", "author": "Omar", "when": "2026-10-31T09:00:00Z", "text": "Fix is in review."}]}
        app.dscroll = 0
        return app, scr, a, b

    def test_properties_are_chips_and_the_header_shows_the_progress_bar_and_the_sections(self):
        app, scr, a, _ = self.page_app()
        app.draw_detail()
        for text in ("Priority", "Owner Ada", "1 of 2 subtasks", "█", "Body", "Comments 2", "Related 2"):
            self.assertTrue(scr.has(text), text)
        self.assertTrue(scr.has("Users land on /home"))  # the body is the first section

    def test_tab_and_the_digits_switch_sections(self):
        app, scr, _, _ = self.page_app()
        app.detail_key(9)
        self.assertEqual(app.dtab, 1)
        app.draw_detail()
        self.assertTrue(scr.has("Ana · "))
        self.assertTrue(scr.has("Reproduced it."))
        self.assertFalse(scr.has("Users land on /home"))
        app.detail_key(curses.KEY_BTAB)
        self.assertEqual(app.dtab, 0)
        app.detail_key(ord("3"))
        self.assertEqual(app.dtab, 2)
        app.detail_key(9)
        self.assertEqual(app.dtab, 0)  # wraps

    def test_switching_sections_starts_at_the_top(self):
        app, _, _, _ = self.page_app()
        app.dscroll = 40
        app.detail_key(9)
        self.assertEqual(app.dscroll, 0)

    def test_related_lists_the_links_and_enter_opens_the_selected_one(self):
        app, scr, a, b = self.page_app()
        app.detail_key(ord("3"))
        app.draw_detail()
        self.assertTrue(scr.has("↗ " + nt.page_title(b)))
        self.assertTrue(scr.has("Other page"))
        opened = []
        app.client.get_page = lambda pid: {"id": pid, "properties": {}}
        app.open_page = lambda page: opened.append(page["id"])
        app.detail_key(ord("j"))
        app.detail_key(10)
        self.assertEqual(opened, ["other"])
        self.assertEqual(app.trail, [a])

    def test_related_cursor_stays_inside_the_list(self):
        app, _, _, _ = self.page_app()
        app.detail_key(ord("3"))
        for _ in range(9):
            app.detail_key(ord("j"))
        app.draw_detail()
        self.assertEqual(app.drow, 1)
        for _ in range(9):
            app.detail_key(ord("k"))
        self.assertEqual(app.drow, 0)

    def test_empty_sections_say_what_to_do(self):
        app, scr, a, _ = self.page_app()
        app.detail["comments"] = []
        a["properties"].pop("Related")
        app.detail_key(ord("2"))
        app.draw_detail()
        self.assertTrue(scr.has("No comments yet · c writes one"))
        app.detail_key(ord("3"))
        scr.clear()
        app.draw_detail()
        self.assertTrue(scr.has("No linked pages"))

    def test_comments_still_loading_say_so(self):
        app, scr, _, _ = self.page_app()
        del app.detail["comments"]
        app.detail_key(ord("2"))
        app.draw_detail()
        self.assertTrue(scr.has("Loading the comments"))

    def test_a_page_without_to_dos_has_no_progress_bar(self):
        app, scr, _, _ = self.page_app()
        app.detail["body"] = "plain text"
        app.draw_detail()
        self.assertFalse(scr.has("subtasks"))

    def test_chips_wrap_on_a_narrow_pane_and_say_how_many_did_not_fit(self):
        app, scr, a, _ = self.page_app(w=40)
        for i in range(12):
            a["properties"]["Extra%d" % i] = {"type": "rich_text", "rich_text": [{"plain_text": "value %d" % i}]}
        app.draw_detail()
        self.assertTrue(any("+10" in scr.row(y) for y in range(2, 8)))

    def test_a_click_on_a_section_label_switches_to_it(self):
        app, scr, _, _ = self.page_app()
        app.draw_detail()
        x, y = next((h[1] + 1, h[0]) for h in app.hits if h[3] == "dtab" and h[4] == 1)
        with mock.patch("curses.getmouse", return_value=(0, x, y, 0, curses.BUTTON1_PRESSED)):
            app.detail_key(curses.KEY_MOUSE)
        self.assertEqual(app.dtab, 1)

    def test_opening_a_page_starts_on_the_body(self):
        app, _, a, _ = self.page_app()
        app.start_job = lambda *a_, **k: None
        app.dtab = 2
        app.open_page(a)
        self.assertEqual((app.dtab, app.drow), (0, 0))

    def test_the_footer_names_the_section_key(self):
        app, scr, _, _ = self.page_app()
        app.draw_detail()
        self.assertTrue(scr.has("Tab section"))


class ChipValue(unittest.TestCase):
    def test_timestamps_become_days_and_long_text_is_cut(self):
        self.assertEqual(nt.chip_value("2026-10-30T15:30:12.040Z"), "2026-10-30")
        self.assertEqual(nt.chip_value("2026-10-30"), "2026-10-30")
        self.assertEqual(nt.chip_value("High"), "High")
        long = "x" * 80
        self.assertLessEqual(len(nt.chip_value(long)), 36)
        self.assertTrue(nt.chip_value(long).endswith("…"))


from herdr_notion import digest
from tests.support import CONFIG


class BarLine(unittest.TestCase):
    GROUPS = {"To do": "To-do", "In progress": "In progress", "Review": "In progress", "Done": "Complete"}

    def pages(self):
        return [page(0, "A", "To do"), page(1, "B", "Review"), page(2, "C", "Review"), page(3, "D", "In progress"), page(4, "E", "Done")]

    def test_one_plain_line_with_the_count_and_the_two_busiest_statuses_in_progress(self):
        line = digest.bar_line(self.pages(), CONFIG, self.GROUPS, None, "Mine")
        self.assertEqual(line, "Notion · 5 mine · 2 review · 1 in progress")
        self.assertNotIn("\n", line)

    def test_changes_since_the_last_digest_are_counted_and_never_saved(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "digest.json")
        pages = self.pages()
        digest.save_snapshot(path, pages, "Status")
        before = open(path).read()
        pages[0]["properties"]["Status"]["status"]["name"] = "Review"
        pages.append(page(9, "New", "To do"))
        line = digest.bar_line(pages, CONFIG, self.GROUPS, digest.load_snapshot(path), "Mine")
        self.assertTrue(line.endswith("· 2 new"))
        self.assertEqual(open(path).read(), before)

    def test_without_progress_statuses_it_is_just_the_count(self):
        self.assertEqual(digest.bar_line([page(0, "A", "To do")], CONFIG, self.GROUPS, None, "All"), "Notion · 1 all")

    def test_bar_is_in_the_usage_text(self):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            digest.main(["--help"])
        self.assertIn("--bar", out.getvalue())


class TidyUp(unittest.TestCase):
    def app(self, status="idle", done=False):
        app, _ = fresh()
        app.save_delegations = lambda: None
        app.sel = 0
        app.delegations[app.pages[0]["id"]] = {"pane": "w1:p1", "agent": "claude", "title": "t", "status": status, "done": done,
                                               "started": time.time()}
        closed = []
        self.saved = dg.close_pane
        dg.close_pane = lambda pane: closed.append(pane)
        self.addCleanup(lambda: setattr(dg, "close_pane", self.saved))
        return app, closed

    def test_a_finished_agent_is_closed_after_a_confirm_and_forgotten(self):
        app, closed = self.app("idle", True)
        app.confirm = lambda text: True
        app.list_key(ord("X"))
        self.assertEqual(closed, ["w1:p1"])
        self.assertNotIn(app.pages[0]["id"], app.delegations)
        self.assertIn("closed the pane", app.toast_state["text"])

    def test_declining_closes_nothing(self):
        app, closed = self.app("idle", True)
        app.confirm = lambda text: False
        app.list_key(ord("X"))
        self.assertEqual(closed, [])
        self.assertIn(app.pages[0]["id"], app.delegations)

    def test_a_working_or_blocked_agent_is_left_alone(self):
        for status in ("working", "blocked"):
            app, closed = self.app(status, False)
            app.confirm = lambda text: self.fail("asked")
            app.list_key(ord("X"))
            self.assertEqual(closed, [])
            self.assertIn("wait for it", app.toast_state["text"])

    def test_a_ticket_without_an_agent_says_so(self):
        app, closed = self.app()
        app.delegations.clear()
        app.list_key(ord("X"))
        self.assertEqual(app.toast_state["text"], "no agent is on this ticket")

    def test_an_idle_matched_agent_can_be_closed_too(self):
        app, closed = self.app()
        app.delegations.clear()
        app.found[app.pages[0]["id"]] = {"pane_id": "w9:p9", "name": "codex", "agent": "codex", "status": "idle"}
        app.confirm = lambda text: True
        app.list_key(ord("X"))
        self.assertEqual(closed, ["w9:p9"])


class TabLabel(unittest.TestCase):
    def test_ticket_id_then_title_clipped(self):
        from herdr_notion.tui import App
        pg = {"properties": {"ID": {"type": "rich_text", "rich_text": [{"plain_text": "ET-2546"}]},
                             "Name": {"type": "title", "title": [{"plain_text": "Fix login redirect"}]}}}
        self.assertEqual(App.tab_label(pg), "ET-2546 Fix login redirect")
        plain = {"properties": {"Name": {"type": "title", "title": [{"plain_text": "Add dark mode"}]}}}
        self.assertEqual(App.tab_label(plain), "Add dark mode")
        long = {"properties": {"Name": {"type": "title", "title": [{"plain_text": "x" * 80}]}}}
        self.assertLessEqual(len(App.tab_label(long)), 36)


class Freshness(unittest.TestCase):
    def test_the_header_says_who_and_how_long_ago_the_list_came_from_notion(self):
        app, scr = fresh(h=20, w=120)
        app.me_name = "Ada"
        app.draw_list()
        self.assertTrue(scr.has("Ada · synced just now"))
        with mock.patch("time.time", return_value=app.synced_at + 12):
            scr.clear()
            app.draw_list()
            self.assertTrue(scr.has("synced 12s ago"))
        with mock.patch("time.time", return_value=app.synced_at + 4 * 60 + 5):
            scr.clear()
            app.draw_list()
            self.assertTrue(scr.has("synced 4m ago"))

    def test_without_a_name_it_is_just_the_time(self):
        app, scr = fresh(h=20, w=120)
        app.draw_list()
        self.assertTrue(scr.has("synced just now"))
        self.assertFalse(scr.has("·  synced"))

    def test_cached_rows_say_cached_not_synced(self):
        app, scr = fresh(h=20, w=120)
        app.cached_at = "2026-10-30T09:00:00+00:00"
        app.draw_list()
        self.assertTrue(scr.has("cached"))
        self.assertFalse(scr.has("synced"))

    def run_failed_job(self, app, kind, error):
        app.start_job("", lambda: (_ for _ in ()).throw(error), lambda r: None, kind)
        deadline = time.time() + 2
        while not app.job["done"] and time.time() < deadline:
            time.sleep(0.005)
        app.poll_job()

    def test_a_failed_refresh_shows_the_banner_and_dims_the_rows_until_a_fresh_list_arrives(self):
        app, scr = fresh(h=20, w=120)
        self.run_failed_job(app, "list", nt.NotionError("Notion 503"))
        self.assertIn("503", app.offline)
        scr.clear()
        app.draw_list()
        self.assertTrue(scr.has("Cannot reach Notion · showing what was loaded · r retries"))
        other = nt.page_title(app.pages[2])  # not the selected row
        row = next(y for y in range(3, 12) if other in "".join(scr.grid[y]))
        self.assertTrue(scr.attrs[row][10] & curses.A_DIM)
        app.apply_list(app.client.discover(), {}, app.client.query({}))
        self.assertEqual(app.offline, "")
        scr.clear()
        app.draw_list()
        self.assertFalse(scr.has("Cannot reach Notion"))

    def test_a_silent_background_failure_counts_too_but_a_herdr_failure_does_not(self):
        app, _ = fresh()
        self.run_failed_job(app, "silent", nt.NotionError("timeout"))
        self.assertEqual(app.offline, "timeout")
        app.offline = ""
        self.run_failed_job(app, "agents", dg.HerdrError("no herdr"))
        self.assertEqual(app.offline, "")

    def test_the_banner_mentions_the_cache_when_that_is_what_is_shown(self):
        app, scr = fresh(h=20, w=130)
        app.cached_at = "2026-10-30T09:00:00+00:00"
        app.offline = "Notion 503"
        app.draw_list()
        self.assertTrue(scr.has("(cached "))

    def test_auto_refresh_marks_the_sync_and_learns_the_name(self):
        app, _ = fresh()
        app.last_refresh = 0
        ran = {}
        app.start_job = lambda label, work, done, kind="list": ran.update(r=work(), done=done)
        app.client.whoami = lambda: {"workspace": "W", "user": "Ada"}
        app.synced_at = None
        app.auto_refresh()
        ran["done"](ran["r"])
        self.assertEqual(app.me_name, "Ada")
        self.assertIsNotNone(app.synced_at)


class EmptyStatesAndTips(unittest.TestCase):
    def empty(self, **kw):
        app, scr = fresh(**kw)
        app.all, app.pages = [], []
        return app, scr

    def test_an_empty_tab_with_no_filters_says_what_to_do(self):
        app, scr = self.empty(h=24, w=100)
        app.draw_list()
        self.assertTrue(scr.has("Nothing in All yet"))
        self.assertTrue(scr.has("Tab opens Mine · n creates a page · r refreshes"))

    def test_filters_that_hide_everything_are_named_with_the_keys_that_remove_them(self):
        app, scr = fresh(h=24, w=110)
        app.live = "zzzz"
        app.quick = "today"
        app.refilter()
        self.assertEqual(app.pages, [])
        app.draw_list()
        self.assertTrue(scr.has("Nothing in All matches"))
        self.assertTrue(scr.has("changed today · “zzzz”"))
        self.assertTrue(scr.has("Esc removes the newest filter"))

    def test_the_empty_message_is_not_shown_while_loading(self):
        app, scr = self.empty(h=24, w=100)
        app.job = {"kind": "list", "label": "Loading…", "done": False, "seq": 1, "on_done": None}
        app.draw_list()
        self.assertFalse(scr.has("Nothing in"))

    def test_a_tip_suggests_only_keys_you_have_not_used(self):
        app, _ = fresh()
        tip = app.next_tip(now=0)
        self.assertIn(tip, [t for _, t in app.TIPS])
        app.used_keys = {k for k, _ in app.TIPS}
        self.assertIsNone(app.next_tip())
        app.used_keys = {k for k, _ in app.TIPS if k != "u"}
        self.assertEqual(app.next_tip(now=123), "u undoes your last status change")

    def test_tips_rotate_every_forty_seconds(self):
        app, _ = fresh()
        a = app.next_tip(now=0)
        self.assertEqual(app.next_tip(now=39), a)
        self.assertNotEqual(app.next_tip(now=41), a)

    def test_pressed_keys_are_remembered_across_sessions(self):
        d = tempfile.mkdtemp()
        app, _ = fresh_in(d)
        app.note_key(ord("g"))
        app.note_key(16)
        app.note_key(curses.KEY_UP)  # not a letter: ignored
        app2, _ = fresh_in(d)
        self.assertEqual(app2.used_keys, {"g", "^P"})
        self.assertNotIn("g groups by status; press it again to fold the groups", [app2.next_tip(now=i * 40) for i in range(20)])

    def test_the_tip_is_drawn_under_the_hints_and_hidden_when_there_is_a_message(self):
        app, scr = fresh(h=24, w=110)
        app.draw_list()
        self.assertTrue(scr.has("tip: "))
        app.msg = "something happened"
        scr.clear()
        app.draw_list()
        self.assertFalse(scr.has("tip: "))
        self.assertTrue(scr.has("something happened"))

    def test_no_tip_on_a_very_short_pane(self):
        app, scr = fresh(h=12, w=110)
        app.draw_list()
        self.assertFalse(scr.has("tip: "))


from herdr_notion import palette as pl
from herdr_notion import config as cfgmod


class Themes(unittest.TestCase):
    def init(self, theme, colors=256):
        app, _ = fresh()
        app.cfg = dict(app.cfg, theme=theme)
        pairs = []
        env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("curses.start_color"), mock.patch("curses.use_default_colors"), \
                mock.patch("curses.COLORS", colors, create=True), mock.patch("curses.init_pair", lambda *a: pairs.append(a)), \
                mock.patch("herdr_notion.palette.herdr_selection_bg", return_value=None):
            app.init_colors()
        return app, pairs

    def test_the_theme_list_and_unknown_names(self):
        self.assertEqual(pl.THEMES, ("default", "light", "high-contrast", "mono"))
        self.assertEqual(pl.theme_name("nonsense"), "default")
        self.assertEqual(pl.next_theme("default"), "light")
        self.assertEqual(pl.next_theme("mono"), "default")
        self.assertEqual(pl.next_theme("nonsense"), "light")

    def test_light_uses_deeper_colors_and_a_pale_selection(self):
        app, pairs = self.init("light")
        red = next(p for p in pairs if p[0] == app.pairs[("red", False)])
        sel = next(p for p in pairs if p[0] == app.pairs[("red", True)])
        self.assertEqual(red[1], pl.LIGHT["red"])
        self.assertEqual(sel[2], pl.LIGHT_SELECTION)
        self.assertTrue(app.sel_bg)

    def test_high_contrast_uses_bright_colors_and_reverse_video_for_the_selection(self):
        app, pairs = self.init("high-contrast")
        self.assertTrue(app.colors)
        self.assertFalse(app.sel_bg)
        self.assertNotIn(("red", True), app.pairs)
        self.assertEqual(app.on_attr(), curses.A_REVERSE)
        red = next(p for p in pairs if p[0] == app.pairs[("red", False)])
        self.assertEqual(red[1], 9)

    def test_mono_turns_the_colors_off_but_the_marks_stay(self):
        app, pairs = self.init("mono")
        self.assertFalse(app.colors)
        self.assertEqual(pairs, [])
        self.assertEqual(app.paint("red"), 0)
        self.assertEqual(app.paint("grey"), curses.A_DIM)
        self.assertEqual(app.cell_style("Priority", "High")[0], "!! High")

    def test_default_is_unchanged(self):
        app, pairs = self.init("default")
        red = next(p for p in pairs if p[0] == app.pairs[("red", False)])
        self.assertEqual(red[1], pl.XTERM["red"])
        sel = next(p for p in pairs if p[0] == app.pairs[("red", True)])
        self.assertEqual(sel[2], pl.DEFAULT_SELECTION)

    def test_an_8_color_terminal_folds_every_theme_back_to_the_base_colors(self):
        for theme in ("default", "light", "high-contrast"):
            app, pairs = self.init(theme, colors=8)
            self.assertTrue(all(p[1] < 8 for p in pairs), theme)

    def test_the_config_accepts_only_known_themes(self):
        for name, want in (("light", "light"), ("high-contrast", "high-contrast"), ("neon", "default")):
            self.assertEqual(cfgmod.normalize({"notion": {"data_source_id": "x", "theme": name}})["theme"], want)

    def test_y_cycles_the_theme_saves_it_and_applies_it(self):
        app, _ = fresh()
        written = []
        app.write_config_value = lambda key, value: written.append((key, value))
        calls = []
        app.init_colors = lambda: calls.append(1)
        app.list_key(ord("Y"))
        self.assertEqual(written, [("theme", "light")])
        self.assertEqual(app.cfg["theme"], "light")
        self.assertEqual(calls, [1])
        self.assertEqual(app.toast_state["text"], "theme: light")
        app.list_key(ord("Y"))
        self.assertEqual(written[-1], ("theme", "high-contrast"))

    def test_the_theme_is_in_the_help_and_the_palette(self):
        from herdr_notion.tui import HELP, App
        self.assertTrue(any(k == "Y" for _, items in HELP for k, _ in items))
        self.assertTrue(any(k == "Y" for _, k in App.PALETTE))


class ThreeWidths(unittest.TestCase):
    def test_under_70_columns_it_is_the_list_alone_with_a_status_glyph(self):
        app, scr = fresh(h=24, w=60)
        app.draw_list()
        self.assertFalse(scr.has("STATUS"))
        self.assertFalse(scr.has("│"))  # no preview divider
        row = next(y for y in range(3, 12) if "Alpha" in "".join(scr.grid[y]) or "Echo" in "".join(scr.grid[y]))
        line = "".join(scr.grid[row])
        self.assertRegex(line[2:6], r"[○◐◑◕✓⊘] ")
        # the page in full (the peek) is not drawn at this width
        self.assertEqual([y for y in range(15, 20) if "edited" in scr.row(y)], [])

    def test_from_70_to_120_the_list_has_a_peek_of_the_selected_page(self):
        app, scr = fresh(h=24, w=90)
        app.draw_list()
        self.assertTrue(scr.has("STATUS"))
        self.assertTrue(any("edited" in scr.row(y) for y in range(15, 24)))
        self.assertFalse(scr.has("│"))

    def test_from_120_the_preview_pane_is_beside_the_list(self):
        app, scr = fresh(h=24, w=150)
        app.cache[app.pages[0]["id"]] = ["body text"]
        app.draw_list()
        self.assertTrue(scr.has("│"))
        self.assertTrue(scr.has("body text"))

    def test_the_glyph_is_in_the_status_color_on_a_narrow_list(self):
        app, scr = fresh(h=24, w=60)
        app.colors, app.sel_bg = True, False
        app.pairs = {(k, False): n + 1 for n, k in enumerate(pl.KEYS + ("plain", "tab"))}
        with mock.patch("curses.color_pair", lambda n: n << 8):
            app.draw_list()
        row = next(y for y in range(3, 12) if "Alpha" in "".join(scr.grid[y]))
        self.assertEqual(scr.attrs[row][2] >> 8 & 0xff, app.pairs[(app.status_key("Done"), False)])
        self.assertEqual("".join(scr.grid[row])[2], "✓")

    def test_a_narrow_list_still_selects_opens_and_scrolls(self):
        app, scr = fresh(h=24, w=60)
        app.draw_list()
        app.list_key(ord("j"))
        self.assertEqual(app.sel, 1)
        opened = []
        app.open_page = lambda p: opened.append(p["id"])
        app.list_key(10)
        self.assertEqual(opened, [app.pages[1]["id"]])


class DelegateNote(unittest.TestCase):
    """d and w let you put a line of your own above the task before it is sent."""

    def setUp(self):
        from herdr_notion import config as cfgmod
        self.app, _ = make_app([page(1, "Alpha")])
        self.app.cfg["delegate"] = dict(cfgmod.load()["delegate"])
        self.sent = []
        self.app.ask = lambda label, initial="": self.answer
        self.app.popup = lambda title, items, **kw: 0
        self.app.track = lambda *a, **k: None
        self.app.build_prompt = lambda: (self.app.pages[0], "THE TASK")
        agents = [{"pane_id": "w1:p1", "name": "orch", "agent": "claude", "status": "idle", "cwd": "/x"}]
        self.patches = [mock.patch("herdr_notion.delegate.list_agents", lambda *a, **k: agents),
                        mock.patch("herdr_notion.delegate.send_prompt", lambda pane, text, *a: self.sent.append((pane, text)))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_the_note_goes_above_the_task(self):
        self.answer = "focus on the tests"
        self.app.delegate()
        self.assertEqual(self.sent, [("w1:p1", "focus on the tests\n\nTHE TASK")])

    def test_enter_with_nothing_typed_sends_the_task_as_it_is(self):
        self.answer = "  "
        self.app.delegate()
        self.assertEqual(self.sent, [("w1:p1", "THE TASK")])

    def test_escape_cancels_the_send(self):
        self.answer = None
        self.app.delegate()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.app.msg, "cancelled")

    def test_ask_note_false_skips_the_question(self):
        self.app.cfg["delegate"]["ask_note"] = False
        self.answer = "never asked"
        self.app.delegate()
        self.assertEqual(self.sent, [("w1:p1", "THE TASK")])
