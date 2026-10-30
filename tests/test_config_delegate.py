import os
import unittest

from herdr_notion import config, delegate, tomlcompat
from herdr_notion.tomlcompat import _Parser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Toml(unittest.TestCase):
    def test_example_config_loads(self):
        with open(os.path.join(ROOT, "config.example.toml"), encoding="utf-8") as f:
            cfg = config.normalize(tomlcompat.loads(f.read()))
        self.assertEqual([f["name"] for f in cfg["filters"]], ["All", "Mine", "Open"])
        self.assertEqual(cfg["filters"][1]["filter"]["people"]["contains"], "{me}")
        self.assertEqual(cfg["delegate"]["agents"][0]["command"], ["claude"])
        self.assertIn("{title}", cfg["delegate"]["prompt"])

    def test_fallback_parser_matches_tomllib(self):
        # Always exercise the fallback, even where tomllib exists.
        with open(os.path.join(ROOT, "config.example.toml"), encoding="utf-8") as f:
            text = f.read()
        mine = _Parser(text).parse()
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib not available to compare against")
        self.assertEqual(mine, tomllib.loads(text))

    def test_fallback_parser_basics(self):
        d = _Parser('a = 1\nb = "x\\ty"\nc = [1,\n 2]\n[t]\nk = { p = true, q = { r = \'s\' } }\n[[t.l]]\nn = 1\n[[t.l]]\nn = 2\n').parse()
        self.assertEqual(d, {"a": 1, "b": "x\ty", "c": [1, 2],
                             "t": {"k": {"p": True, "q": {"r": "s"}}, "l": [{"n": 1}, {"n": 2}]}})


class Normalize(unittest.TestCase):
    def test_missing_database_means_setup(self):
        with self.assertRaises(config.NeedsSetup):
            config.normalize({"notion": {"api_token_cmd": "x"}})
        with self.assertRaises(config.NeedsSetup):
            config.normalize({})

    def test_data_source_id_alone_is_enough(self):
        cfg = config.normalize({"notion": {"data_source_id": "ds"}})
        self.assertEqual((cfg["database_id"], cfg["data_source_id"]), ("", "ds"))

    def test_missing_file_means_setup(self):
        with self.assertRaises(config.NeedsSetup):
            config.load("/nonexistent/dir/config.toml")

    def test_defaults(self):
        cfg = config.normalize({"notion": {"database_id": "d", "api_token_cmd": "echo t"}})
        self.assertEqual(cfg["filters"], [{"name": "All"}])
        self.assertEqual(cfg["status_property"], "Status")

    def test_token_cmd_failure_does_not_echo_output(self):
        with self.assertRaises(config.ConfigError) as cm:
            config.read_token("echo secret; exit 3")
        self.assertNotIn("secret", str(cm.exception))
        self.assertEqual(config.read_token("echo tok"), "tok")


class Token(unittest.TestCase):
    def test_order_cmd_then_env_then_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "token")
            with self.assertRaises(config.NeedsSetup):
                config.resolve_token({}, environ={}, path=path)
            config.save_token("  from-file ", path)
            self.assertEqual(config.resolve_token({}, environ={}, path=path), "from-file")
            self.assertEqual(config.resolve_token({}, environ={"NOTION_TOKEN": "env"}, path=path), "env")
            self.assertEqual(config.resolve_token({"api_token_cmd": "echo cmd"}, environ={"NOTION_TOKEN": "env"}, path=path), "cmd")

    def test_token_file_is_owner_only(self):
        import stat
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "token")
            config.save_token("x", path)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            config.save_token("y", path)  # overwrite keeps the mode
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class Delegate(unittest.TestCase):
    def test_list_agents_and_label(self):
        resp = {"result": {"agents": [
            {"pane_id": "w1:p1", "agent": "claude", "name": "main", "agent_status": "idle", "cwd": "/a/b"}]}}
        agents = delegate.list_agents(lambda args: resp)
        self.assertEqual(agents[0]["pane_id"], "w1:p1")
        self.assertIn("idle", delegate.agent_label(agents[0], home="/zzz"))
        self.assertIn("/a/b", delegate.agent_label(agents[0], home="/zzz"))

    def test_unique_cwds(self):
        agents = [{"cwd": "/a"}, {"cwd": "/b"}, {"cwd": "/a"}]
        self.assertEqual(delegate.unique_cwds(agents), ["/a", "/b"])

    def test_send_prompt_uses_agent_prompt(self):
        calls = []
        delegate.send_prompt("w1:p1", "hello", lambda args: calls.append(args) or {})
        self.assertEqual(calls, [["agent", "prompt", "w1:p1", "hello"]])

    def test_start_agent_sequence(self):
        calls = []

        def fake(args, timeout=60):
            calls.append(args)
            if args[:2] == ["tab", "create"]:
                return {"result": {"root_pane": {"pane_id": "w1:p9"}}}
            return {}

        cfg = {"startup_delay_ms": 0, "wait_ready_ms": 1000}
        pane = delegate.start_agent(["claude", "--x y"], "/tmp", "T", cfg, workspace="w1",
                                    runner=fake, sleep=lambda s: None)
        self.assertEqual(pane, "w1:p9")
        self.assertEqual(calls[0], ["tab", "create", "--workspace", "w1", "--cwd", "/tmp", "--label", "T", "--no-focus"])
        self.assertEqual(calls[1], ["pane", "run", "w1:p9", "claude '--x y'"])
        self.assertEqual(calls[2], ["agent", "get", "w1:p9"])
        self.assertEqual(calls[3][:5], ["agent", "wait", "w1:p9", "--until", "idle"])


if __name__ == "__main__":
    unittest.main()


class TokenOverrideTest(unittest.TestCase):
    def test_reports_the_source_that_beats_the_token_file(self):
        self.assertEqual(config.token_override({}, {}), "")
        self.assertEqual(config.token_override({}, {"NOTION_TOKEN": "x"}), "$NOTION_TOKEN")
        self.assertIn("api_token_cmd", config.token_override({"api_token_cmd": "echo x"}, {"NOTION_TOKEN": "x"}))
