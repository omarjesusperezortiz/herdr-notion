import unittest

from herdr_notion.tui import App


def app_with(delegations):
    a = App.__new__(App)  # no curses screen needed for the tracking logic
    a.delegations, a.finished = delegations, []
    a.save_delegations = lambda: None
    return a


def agent(status, pane="p1"):
    return {"pane_id": pane, "status": status}


class Tracking(unittest.TestCase):
    def d(self):
        return {"pane": "p1", "agent": "claude", "title": "t", "seen_working": False, "done": False, "status": "sent"}

    def test_finished_only_after_it_was_seen_working(self):
        a = app_with({"pg": self.d()})
        a.on_agents([agent("idle")])          # idle right after sending: the agent has not started yet
        self.assertEqual(a.finished, [])
        a.on_agents([agent("working")])
        a.on_agents([agent("idle")])
        self.assertEqual(a.finished, ["pg"])
        self.assertTrue(a.delegations["pg"]["done"])

    def test_done_state_counts_as_finished_and_is_offered_once(self):
        a = app_with({"pg": self.d()})
        a.on_agents([agent("working")])
        a.on_agents([agent("done")])
        a.on_agents([agent("idle")])
        self.assertEqual(a.finished, ["pg"])

    def test_a_closed_pane_ends_tracking_quietly(self):
        a = app_with({"pg": self.d()})
        a.on_agents([agent("working", pane="other")])
        self.assertTrue(a.delegations["pg"]["done"])
        self.assertEqual(a.finished, [])
