"""`python3 -m herdr_notion token`: replace the saved Notion token from a terminal."""
import getpass
import sys

from . import config as cfgmod
from . import notion as nt


def main():
    try:
        cfg = cfgmod.load()
    except cfgmod.NeedsSetup:
        cfg = {}
    except cfgmod.ConfigError as e:
        sys.exit("config problem: %s" % e)
    try:
        token = getpass.getpass("New Notion integration secret (hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit("\ncancelled, nothing changed")
    if not token:
        sys.exit("empty input, nothing changed")
    try:
        me = nt.Client(token, "").request("GET", "/users/me")
    except nt.NotionError as e:
        sys.exit("Notion rejected that token: %s\nNothing changed." % e)
    cfgmod.save_token(token)
    print("Saved for %s -> %s (mode 600)" % (me.get("name") or "the integration", cfgmod.token_path()))
    over = cfgmod.token_override(cfg)
    if over:
        print("Note: %s is set and takes precedence over this file. Change or unset it too." % over)
