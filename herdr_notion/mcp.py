"""Minimal MCP client (JSON-RPC over Streamable HTTP) for Notion's hosted server."""
import json
import urllib.error
import urllib.request

from . import notion as nt
from . import oauth

URL = oauth.MCP_HOST + "/mcp"
PROTOCOL = "2025-06-18"


class McpError(nt.NotionError):
    pass


class Mcp:
    def __init__(self, token_fn=oauth.access_token, url=URL):
        self.token_fn = token_fn
        self.url = url
        self.session = None
        self.next_id = 1
        self.ready = False

    def _post(self, payload, want_reply=True):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "Authorization": "Bearer " + self.token_fn(), "User-Agent": "herdr-notion",
                   "MCP-Protocol-Version": PROTOCOL}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                self.session = r.headers.get("Mcp-Session-Id") or self.session
                body = r.read().decode()
                ctype = r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            raise McpError("Notion MCP %d: %s" % (e.code, e.read().decode(errors="replace")[:200]))
        except (urllib.error.URLError, OSError) as e:
            raise McpError("Notion MCP: %s" % e)
        if not want_reply or not body.strip():
            return None
        if "text/event-stream" in ctype:
            for line in body.splitlines():
                if line.startswith("data:") and line[5:].strip():
                    msg = json.loads(line[5:])
                    if msg.get("id") == payload.get("id"):
                        return msg
            raise McpError("no reply in event stream")
        return json.loads(body)

    def rpc(self, method, params=None):
        if not self.ready and method != "initialize":
            self.start()
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        self.next_id += 1
        reply = self._post(msg)
        if reply is None or "error" in reply:
            raise McpError("%s: %s" % (method, (reply or {}).get("error", "empty reply")))
        return reply["result"]

    def start(self):
        self.ready = True  # avoid recursion
        self.rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                "clientInfo": {"name": "herdr-notion", "version": "0.1.0"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, want_reply=False)

    def tools(self):
        return self.rpc("tools/list").get("tools", [])

    def call(self, name, arguments=None):
        res = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        if res.get("isError"):
            raise McpError("%s: %s" % (name, text[:300]))
        try:
            return json.loads(text)
        except ValueError:
            return text
