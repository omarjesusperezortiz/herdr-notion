import curses
import unittest
from unittest import mock

from tests.support import make_app, page  # noqa: E402


def fresh(h=30, w=100):
    return make_app([page(1, "Alpha"), page(2, "Bravo")], h=h, w=w)


class Confirm(unittest.TestCase):
    def ask(self, keys, h=30, w=100, text="Set Status to 'Review'?"):
        app, scr = fresh(h, w)
        scr.keys = list(keys)
        seen = []
        real = scr.refresh
        scr.refresh = lambda: (seen.append(scr.text()), real())[1]
        return app.confirm(text), scr, seen

    def test_enter_and_y_say_yes(self):
        for k in ("\n", "\r", "y", "Y", curses.KEY_ENTER):
            self.assertTrue(self.ask([k])[0], k)

    def test_escape_n_q_and_any_other_key_say_no(self):
        for k in ("\x1b", "n", "N", "q", " ", "x"):
            self.assertFalse(self.ask([k])[0], k)

    def test_a_resize_or_a_mouse_event_asks_again_instead_of_answering(self):
        with mock.patch("curses.getmouse", return_value=(0, 1, 1, 0, 0)):
            ok, _, seen = self.ask([curses.KEY_RESIZE, curses.KEY_MOUSE, "y"])
        self.assertTrue(ok)
        self.assertEqual(len(seen), 3)

    def test_the_question_is_a_box_in_the_middle_with_its_buttons(self):
        ok, scr, seen = self.ask(["\x1b"], h=30, w=100)
        text = seen[0].split("\n")
        y = next(i for i, l in enumerate(text) if "Set Status to 'Review'?" in l)
        x = text[y].index("Set")
        self.assertTrue(10 <= y <= 18, y)
        self.assertTrue(15 <= x <= 30, x)
        self.assertTrue(any("Confirm" in l and "╭" in l for l in text))
        self.assertTrue(any("⏎ Yes    Esc cancel" in l for l in text))

    def test_the_list_stays_visible_behind_the_box(self):
        ok, scr, seen = self.ask(["\x1b"])
        self.assertIn("Alpha", seen[0])

    def test_a_long_question_wraps_inside_the_box(self):
        ok, scr, seen = self.ask(["\x1b"], w=60, text="word " * 40)
        self.assertTrue(all(len(l) <= 60 for l in seen[0].split("\n")))

    def test_without_a_list_yet_the_box_still_draws(self):
        app, scr = fresh()
        app.cfg = None
        scr.keys = ["y"]
        self.assertTrue(app.confirm("Continue?"))


class TypeText(unittest.TestCase):
    def run_it(self, keys, initial=""):
        app, scr = fresh()
        scr.keys = list(keys)
        seen = []
        real = scr.refresh
        scr.refresh = lambda: (seen.append(scr.text()), real())[1]
        with mock.patch("curses.curs_set", lambda n: None):
            return app.type_text("Send “Alpha”", [("grey", "To orch (idle)")], "note › ", initial), seen

    def test_enter_returns_what_was_typed(self):
        got, seen = self.run_it(list("hi there") + ["\n"])
        self.assertEqual(got, "hi there")
        self.assertTrue(any("note › hi there▏" in s for s in seen))

    def test_backspace_deletes_and_escape_cancels(self):
        self.assertEqual(self.run_it(["a", "b", curses.KEY_BACKSPACE, "\n"])[0], "a")
        self.assertIsNone(self.run_it(["a", "\x1b"])[0])

    def test_an_empty_line_is_a_valid_answer(self):
        self.assertEqual(self.run_it(["\n"])[0], "")

    def test_the_box_shows_the_lines_and_the_key_help_in_the_middle(self):
        _, seen = self.run_it(["\n"])
        text = seen[0].split("\n")
        y = next(i for i, l in enumerate(text) if "To orch (idle)" in l)
        self.assertTrue(10 <= y <= 18)
        self.assertTrue(any("⏎ continue    Esc cancel" in l for l in text))


class Review(unittest.TestCase):
    def test_it_shows_every_line_and_accepts_with_enter_or_y(self):
        for key, want in (("\n", True), ("y", True), ("\x1b", False), ("n", False)):
            app, scr = fresh()
            scr.keys = [key]
            seen = []
            real = scr.refresh
            scr.refresh = lambda: (seen.append(scr.text()), real())[1]
            got = app.review("What will be sent", [("grey", "To orch"), ("", ""), ("", "the note"), ("", "THE TASK")])
            self.assertEqual(got, want, key)
            self.assertIn("What will be sent", seen[0])
            self.assertIn("THE TASK", seen[0])
            self.assertIn("⏎ Send    Esc cancel", seen[0])


class Toasts(unittest.TestCase):
    def test_ok_info_and_error_toasts_have_their_own_life(self):
        app, scr = fresh()
        for kind, life in (("ok", 3.0), ("info", 6.0), ("err", 60.0)):
            with mock.patch("time.time", return_value=1000.0):
                app.toast("x", kind)
            self.assertEqual(app.toast_state["until"], 1000.0 + life, kind)

    def test_an_ok_toast_is_a_small_box_in_the_middle_and_fades(self):
        app, scr = fresh()
        app.toast("sent “Alpha” to orchestrator")
        app.redraw()
        y, x = scr.find("✓ sent “Alpha” to orchestrator")
        self.assertTrue(10 <= y <= 18)
        self.assertTrue(20 <= x <= 45)
        app.toast_state["until"] = 0
        app.redraw()
        self.assertFalse(scr.has("sent “Alpha”"))

    def test_the_modal_never_leaves_the_screen(self):
        app, scr = fresh(h=8, w=30)
        app.toast("word " * 50)
        app.redraw()
        self.assertTrue(all(len(l) <= 30 for l in scr.text().split("\n")))


if __name__ == "__main__":
    unittest.main()
