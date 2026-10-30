"""Browser sign-in for Notion's hosted MCP server: OAuth 2.0 authorization code + PKCE,
dynamic client registration (no client secret), loopback redirect, refresh tokens.
Standard library only. The login is stored in oauth.json (0600) in the config dir."""
import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from . import config as cfgmod

MCP_HOST = "https://mcp.notion.com"
CLIENT_NAME = "herdr-notion"
SKEW = 60  # refresh this many seconds before the access token expires


class OAuthError(Exception):
    pass


def store_path():
    return os.path.join(os.path.dirname(cfgmod.config_path()), "oauth.json")


def _http(url, data=None, headers=None, method=None, timeout=30):
    body = None
    h = {"Accept": "application/json", "User-Agent": "herdr-notion"}
    h.update(headers or {})
    if data is not None:
        if h.get("Content-Type") == "application/json":
            body = json.dumps(data).encode()
        else:
            body = urllib.parse.urlencode(data).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")[:300]
        raise OAuthError("%s -> %d %s" % (url.split("?")[0], e.code, text))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise OAuthError("%s: %s" % (url.split("?")[0], e))


def discover(host=MCP_HOST):
    res = _http(host + "/.well-known/oauth-protected-resource")
    servers = res.get("authorization_servers") or []
    if not servers:
        raise OAuthError("no authorization server advertised")
    meta = _http(servers[0].rstrip("/") + "/.well-known/oauth-authorization-server")
    for k in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        if not meta.get(k):
            raise OAuthError("server metadata lacks %s" % k)
    return meta


def pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(meta, redirect_uri):
    res = _http(meta["registration_endpoint"], {
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }, {"Content-Type": "application/json"}, method="POST")
    if not res.get("client_id"):
        raise OAuthError("registration returned no client_id")
    return res["client_id"]


class _Callback(http.server.BaseHTTPRequestHandler):
    result = None
    start_url = ""  # /start redirects here: a short, clickable stand-in for the long authorization link

    def do_GET(self):
        if self.path.split("?")[0] == "/start" and type(self).start_url:
            self.send_response(302)
            self.send_header("Location", type(self).start_url)
            self.end_headers()
            return
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in q or "error" in q:
            type(self).result = {k: v[0] for k, v in q.items()}
            page = "<h2>Signed in to Notion.</h2><p>You can close this tab and return to herdr.</p>"
        else:
            page = "<p>Waiting for Notion…</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(("<!doctype html><meta charset=utf-8><body style='font:16px system-ui;margin:3em'>" + page).encode())

    def log_message(self, *a):
        pass


def _save(tokens, client_id):
    data = {
        "client_id": client_id,
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": int(time.time()) + int(tokens.get("expires_in") or 3600),
        "workspace_name": tokens.get("workspace_name") or "",
        "workspace_id": tokens.get("workspace_id") or "",
    }
    path = store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.chmod(path, 0o600)
    return data


def load():
    try:
        with open(store_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def logout(revoke=True):
    """Forget the login. Notion is told to revoke it first (best effort: offline still signs you out locally).
    Returns True when there was a login to remove."""
    data = load()
    if data and revoke:
        try:
            meta = discover()
            token = data.get("refresh_token") or data.get("access_token")
            if meta.get("revocation_endpoint") and token:
                _http(meta["revocation_endpoint"], {"token": token, "client_id": data["client_id"]}, method="POST", timeout=10)
        except OAuthError:
            pass
    try:
        os.remove(store_path())
        return True
    except OSError:
        return False


class Flow:
    """One browser sign-in. Create it, show `url`, then call `poll()` until it returns a result
    (the browser reached the local address) or hand `parse(pasted_address)` to `finish()`."""

    def __init__(self):
        self.meta = discover()
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Callback)
        _Callback.result = None
        self.redirect = "http://127.0.0.1:%d/callback" % self.server.server_address[1]
        self.client_id = register(self.meta, self.redirect)
        self.verifier, challenge = pkce()
        self.state = secrets.token_urlsafe(24)
        self.url = self.meta["authorization_endpoint"] + "?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect,
            "state": self.state, "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "consent"})
        _Callback.start_url = self.url
        self.short = "http://127.0.0.1:%d/start" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def poll(self):
        return _Callback.result

    @staticmethod
    def parse(pasted):
        q = urllib.parse.parse_qs(urllib.parse.urlparse((pasted or "").strip()).query)
        return {k: v[0] for k, v in q.items()}

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def finish(self, res):
        """Check the callback values, trade the code for tokens and store them."""
        self.close()
        if not res:
            raise OAuthError("no sign-in result")
        if res.get("error"):
            raise OAuthError("%s %s" % (res["error"], res.get("error_description", "")))
        if res.get("state") != self.state:
            raise OAuthError("state mismatch; sign-in aborted")
        if not res.get("code"):
            raise OAuthError("the address has no code; paste the whole address from the browser")
        tokens = _http(self.meta["token_endpoint"], {
            "grant_type": "authorization_code", "code": res["code"], "client_id": self.client_id,
            "redirect_uri": self.redirect, "code_verifier": self.verifier}, method="POST")
        if not tokens.get("access_token"):
            raise OAuthError("token response had no access_token")
        return _save(tokens, self.client_id)


def login(say=print, open_browser=webbrowser.open, timeout=240, ask=None):
    """Command-line flow. `ask(prompt)` is the paste fallback for a browser on another machine."""
    flow = Flow()
    say("Opening your browser to sign in to Notion. If nothing opens, visit:\n%s" % flow.url)
    try:
        open_browser(flow.url)
    except Exception:
        pass
    if ask:
        res = flow.parse(ask("After approving, paste the address the browser ends up on: "))
    else:
        end = time.time() + timeout
        while flow.poll() is None and time.time() < end:
            time.sleep(0.2)
        res = flow.poll()
        if not res:
            flow.close()
            raise OAuthError("timed out waiting for the browser sign-in")
    return flow.finish(res)


def access_token():
    """A valid access token, refreshing it when it is about to expire."""
    data = load()
    if not data:
        raise OAuthError("not signed in")
    if data["expires_at"] - SKEW > time.time():
        return data["access_token"]
    if not data.get("refresh_token"):
        raise OAuthError("login expired; sign in again")
    meta = discover()
    tokens = _http(meta["token_endpoint"], {"grant_type": "refresh_token", "refresh_token": data["refresh_token"],
                                            "client_id": data["client_id"]}, method="POST")
    tokens.setdefault("refresh_token", data["refresh_token"])  # rotate when the server sends a new one
    tokens["workspace_name"] = data.get("workspace_name")
    tokens["workspace_id"] = data.get("workspace_id")
    return _save(tokens, data["client_id"])["access_token"]
