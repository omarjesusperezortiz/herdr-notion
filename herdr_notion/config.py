"""Config loading. The file lives in the herdr-managed plugin config dir."""
import os
import subprocess

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
        "api_token_cmd": n.get("api_token_cmd", ""),
        "status_property": n.get("status_property", "Status"),
        "columns": list(n.get("columns", [])),
        "title_property": n.get("title_property", ""),
        "me_user_id": n.get("me_user_id", ""),
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


def token_path():
    return os.path.join(os.path.dirname(config_path()), "token")


def save_token(token, path=None):
    """Write the token owner-readable only (0600), replacing any old file."""
    path = path or token_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token.strip() + "\n")
    os.chmod(path, 0o600)


def resolve_token(cfg, environ=None, path=None):
    """Token from api_token_cmd, else $NOTION_TOKEN, else the token file."""
    environ = os.environ if environ is None else environ
    if cfg.get("api_token_cmd"):
        return read_token(cfg["api_token_cmd"])
    if environ.get("NOTION_TOKEN"):
        return environ["NOTION_TOKEN"].strip()
    try:
        with open(path or token_path(), encoding="utf-8") as f:
            token = f.read().strip()
    except FileNotFoundError:
        token = ""
    if not token:
        raise NeedsSetup("no Notion token found")
    return token


def read_token(cmd):
    """Run the user's token command; never log or echo its output."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        raise ConfigError("api_token_cmd timed out")
    token = r.stdout.strip()
    if r.returncode != 0 or not token:
        raise ConfigError("api_token_cmd failed (exit %d); check the command" % r.returncode)
    return token
