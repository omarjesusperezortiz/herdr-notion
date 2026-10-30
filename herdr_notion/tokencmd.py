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


def login_main():
    """`python3 -m herdr_notion login|logout`: browser sign-in to Notion's hosted MCP server."""
    from . import mcp, oauth
    if sys.argv[1] == "logout":
        print("signed out" if oauth.logout() else "was not signed in")
        return
    try:
        info = oauth.login(ask=input if "--paste" in sys.argv else None, open_browser=(lambda u: None) if "--paste" in sys.argv else __import__("webbrowser").open)
        client = mcp.Mcp()
        names = [t["name"] for t in client.tools()]
    except (oauth.OAuthError, mcp.McpError) as e:
        sys.exit("sign-in failed: %s" % e)
    print("Signed in%s. Notion MCP offers %d tools." % (
        " to %s" % info["workspace_name"] if info.get("workspace_name") else "", len(names)))
    print("Login stored in %s (mode 600)" % oauth.store_path())
