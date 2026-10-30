"""Config loading. The file lives in the herdr-managed plugin config dir."""
import os

from . import tomlcompat

DEFAULT_PROMPT = """\
You are asked to work on this Notion page: {title}
Link: {url}

Properties:
{properties}

Content:
{body}
"""


class ConfigError(Exception):
    pass


class NeedsSetup(ConfigError):
    """No usable config or token yet: the first-run wizard should run."""


def config_path():
    d = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if not d:
        d = os.path.expanduser("~/.config/herdr/plugins/config/herdr-notion")
    return os.path.join(d, "config.toml")


def load(path=None):
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as f:
            raw = tomlcompat.loads(f.read())
    except FileNotFoundError:
        raise NeedsSetup("config not found: %s" % path)
    except ValueError as e:
        raise ConfigError("%s: %s" % (path, e))
    return normalize(raw)


def normalize(raw):
    n = raw.get("notion", {})
    if not n.get("database_id") and not n.get("data_source_id"):
        raise NeedsSetup("[notion] database_id is not set")
    d = raw.get("delegate", {})
    return {
        "database_id": n.get("database_id", ""),
        "data_source_id": n.get("data_source_id", ""),
        "status_property": n.get("status_property", "Status"),
        "columns": list(n.get("columns", [])),
        "title_property": n.get("title_property", ""),
        "page_size": int(n.get("page_size", 50)),
        "filters": raw.get("filters") or [{"name": "All"}],
        "delegate": {
            "prompt": d.get("prompt", DEFAULT_PROMPT),
            "max_body_chars": int(d.get("max_body_chars", 6000)),
            "default_cwd": d.get("default_cwd", ""),
            "startup_delay_ms": int(d.get("startup_delay_ms", 1500)),
            "wait_ready_ms": int(d.get("wait_ready_ms", 30000)),
            "agents": d.get("agents") or [{"name": "claude", "command": ["claude"]}],
        },
    }
