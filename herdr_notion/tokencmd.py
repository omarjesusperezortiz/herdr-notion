"""`python3 -m herdr_notion login|logout`: browser sign-in to Notion from a terminal."""
import sys


def login_main():
    from . import mcpbackend, oauth
    if sys.argv[1] == "logout":
        print("signed out" if oauth.logout() else "was not signed in")
        return
    paste = "--paste" in sys.argv
    try:
        info = oauth.login(ask=input if paste else None,
                           open_browser=(lambda u: None) if paste else __import__("webbrowser").open)
        who = mcpbackend.McpClient("").whoami()
    except (oauth.OAuthError, Exception) as e:
        sys.exit("sign-in failed: %s" % e)
    print("Signed in to %s." % (who["workspace"] or info.get("workspace_name") or "Notion"))
    print("Login stored in %s (mode 600)" % oauth.store_path())
