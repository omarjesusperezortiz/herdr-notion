"""Notion REST client (urllib) plus pure helpers: filters, flattening."""
import json
import urllib.error
import urllib.request

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"  # data sources: queries go to /data_sources/{id}/query


class NotionError(Exception):
    pass


# ---------------------------------------------------------------- pure helpers

def substitute(obj, values):
    """Replace {name} placeholders in every string of a filter structure."""
    if isinstance(obj, str):
        for k, v in values.items():
            obj = obj.replace("{%s}" % k, v)
        return obj
    if isinstance(obj, list):
        return [substitute(x, values) for x in obj]
    if isinstance(obj, dict):
        return {k: substitute(v, values) for k, v in obj.items()}
    return obj


def build_query(flt, query=None, title_property="Name", me="", page_size=50, cursor=None):
    """Build a data source query body from a config filter and optional search text.

    `flt` is a config entry: {name, filter?, sort?}. The search text is ANDed
    with the filter as a title `contains` condition.
    """
    body = {"page_size": page_size}
    conds = []
    if flt.get("filter"):
        conds.append(substitute(flt["filter"], {"me": me}))
    if query:
        conds.append({"property": title_property, "title": {"contains": query}})
    if len(conds) == 1:
        body["filter"] = conds[0]
    elif conds:
        body["filter"] = {"and": conds}
    sort = flt.get("sort")
    if sort:
        body["sorts"] = [{"property": sort["property"],
                          "direction": sort.get("direction", "descending")}]
    if cursor:
        body["start_cursor"] = cursor
    return body


def rich_text(parts):
    return "".join(p.get("plain_text", "") for p in parts or [])


def prop_text(prop):
    """Render one page property value as plain text."""
    if not prop:
        return ""
    t = prop.get("type")
    v = prop.get(t)
    if t in ("title", "rich_text"):
        return rich_text(v)
    if t in ("select", "status"):
        return (v or {}).get("name", "")
    if t == "multi_select":
        return ", ".join(x["name"] for x in v or [])
    if t == "people":
        return ", ".join(x.get("name") or x.get("id", "") for x in v or [])
    if t == "date":
        if not v:
            return ""
        return v["start"] + (" → " + v["end"] if v.get("end") else "")
    if t == "checkbox":
        return "yes" if v else "no"
    if t in ("number", "url", "email", "phone_number", "created_time", "last_edited_time"):
        return "" if v is None else str(v)
    if t == "unique_id":
        return ("%s-%s" % (v["prefix"], v["number"])) if v.get("prefix") else str(v["number"])
    if t == "formula":
        return prop_text({"type": v["type"], v["type"]: v.get(v["type"])}) if v else ""
    if t == "rollup":
        if v and v.get("type") in ("number", "date"):
            return prop_text({"type": v["type"], v["type"]: v.get(v["type"])})
        return ""
    if t == "relation":
        return "%d linked" % len(v or []) if v else ""
    if t == "files":
        return ", ".join(f.get("name", "") for f in v or [])
    if t in ("created_by", "last_edited_by"):
        return (v or {}).get("name", "")
    return ""


def page_title(page):
    for p in page.get("properties", {}).values():
        if p.get("type") == "title":
            return rich_text(p["title"]) or "(untitled)"
    return "(untitled)"


def title_property_name(properties):
    for name, p in properties.items():
        if p.get("type") == "title":
            return name
    return "Name"


def page_properties(page):
    """Ordered [(name, text)] for non-empty, non-title properties."""
    out = []
    for name, p in page.get("properties", {}).items():
        if p.get("type") == "title":
            continue
        text = prop_text(p)
        if text:
            out.append((name, text))
    return out


def flatten_blocks(blocks, depth=0, children=None):
    """Flatten Notion blocks to plain text lines.

    `children` maps a block id to its already fetched child blocks, so this
    function stays free of network access.
    """
    children = children or {}
    lines = []
    ind = "  " * depth
    num = 0
    for b in blocks:
        t = b.get("type")
        v = b.get(t) or {}
        text = rich_text(v.get("rich_text")) if isinstance(v, dict) else ""
        num = num + 1 if t == "numbered_list_item" else 0
        if t in ("paragraph", "quote", "callout"):
            line = ("> " if t == "quote" else "") + text
        elif t.startswith("heading_"):
            line = "#" * int(t[-1]) + " " + text
        elif t == "bulleted_list_item":
            line = "- " + text
        elif t == "numbered_list_item":
            line = "%d. %s" % (num, text)
        elif t == "to_do":
            line = "[%s] %s" % ("x" if v.get("checked") else " ", text)
        elif t == "toggle":
            line = "▸ " + text
        elif t == "code":
            line = "```%s\n%s\n```" % (v.get("language", ""), text)
        elif t == "divider":
            line = "---"
        elif t == "child_page":
            line = "[page] " + v.get("title", "")
        elif t == "child_database":
            line = "[database] " + v.get("title", "")
        elif t in ("bookmark", "embed", "link_preview"):
            line = v.get("url", "")
        elif t in ("image", "file", "pdf", "video", "audio"):
            ref = v.get(v.get("type", ""), {}) or {}
            line = "[%s] %s" % (t, rich_text(v.get("caption")) or ref.get("url", ""))
        else:
            line = text
        if line or t == "paragraph":
            lines.extend((ind + l) if l else "" for l in line.split("\n"))
        lines.extend(flatten_blocks(children.get(b.get("id"), []), depth + 1, children))
    return lines


def status_options(schema, prop_name):
    """Return (kind, [option names]) for a status/select property in a data source."""
    p = (schema.get("properties") or {}).get(prop_name)
    if not p or p.get("type") not in ("status", "select"):
        raise NotionError("property %r is not a status/select property" % prop_name)
    kind = p["type"]
    return kind, [o["name"] for o in p[kind].get("options", [])]


def status_groups(schema, prop_name):
    """Map option name -> group name (to_do, in_progress, complete) for a status property."""
    p = (schema.get("properties") or {}).get(prop_name) or {}
    st = p.get("status") or {}
    by_id = {o.get("id"): o["name"] for o in st.get("options", [])}
    out = {}
    for g in st.get("groups", []):
        for oid in g.get("option_ids", []):
            if oid in by_id:
                out[by_id[oid]] = g.get("name", "")
    return out


def group_kind(group_name):
    """Normalize a group name to 'todo' | 'progress' | 'done' | ''."""
    g = (group_name or "").lower().replace(" ", "_")
    if g in ("to_do", "todo"):
        return "todo"
    if g == "in_progress":
        return "progress"
    if g in ("complete", "done"):
        return "done"
    return ""


def relative_time(iso, now=None):
    """'5m', '2h', '3d', '6w', '2y' for an ISO-8601 timestamp; '' if unparsable."""
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    secs = max(0, int(((now or datetime.now(timezone.utc)) - t).total_seconds()))
    for unit, n in (("y", 31536000), ("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n:
            return "%d%s" % (secs // n, unit)
    return "now"


def status_patch(prop_name, kind, option):
    return {"properties": {prop_name: {kind: {"name": option}}}}


def render_prompt(template, page, body, max_body_chars=6000):
    """Fill the delegate prompt template."""
    if len(body) > max_body_chars:
        body = body[:max_body_chars] + "\n[truncated]"
    props = page_properties(page)
    status = ""
    for p in page.get("properties", {}).values():
        if p.get("type") == "status":
            status = prop_text(p)
            break
    values = {
        "title": page_title(page),
        "url": page.get("url", ""),
        "id": page.get("id", ""),
        "status": status,
        "properties": "\n".join("- %s: %s" % kv for kv in props),
        "body": body,
    }
    out = template
    for k, v in values.items():  # plain replace: page text may contain braces
        out = out.replace("{%s}" % k, v)
    return out


# ---------------------------------------------------------------------- client

class Client:
    def __init__(self, token, database_id, data_source_id=""):
        self.token = token
        self.database_id = database_id
        self.data_source_id = data_source_id
        self._schema = None

    def request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token,
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("message", "")
            except Exception:
                msg = ""
            raise NotionError("Notion %d: %s" % (e.code, msg or e.reason))
        except urllib.error.URLError as e:
            raise NotionError("network error: %s" % e.reason)

    def discover(self):
        """Resolve the data source id and cache its schema."""
        if self._schema:
            return self._schema
        if not self.data_source_id:
            db = self.request("GET", "/databases/" + self.database_id)
            sources = db.get("data_sources") or []
            if not sources:
                raise NotionError("database has no data sources (is it shared with the integration?)")
            self.data_source_id = sources[0]["id"]
        self._schema = self.request("GET", "/data_sources/" + self.data_source_id)
        return self._schema

    def query(self, body):
        self.discover()
        return self.request("POST", "/data_sources/%s/query" % self.data_source_id, body)

    def get_page(self, page_id):
        return self.request("GET", "/pages/" + page_id)

    def body_lines(self, block_id, max_depth=2):
        """Fetch children (paginated, nested up to max_depth) and flatten them."""
        kids = {}

        def fetch(bid, depth):
            blocks = self._children(bid)
            if depth < max_depth:
                for b in blocks:
                    if b.get("has_children") and b.get("type") != "child_page":
                        kids[b["id"]] = fetch(b["id"], depth + 1)
            return blocks

        return flatten_blocks(fetch(block_id, 0), 0, kids)

    def _children(self, block_id):
        out, cursor = [], None
        while True:
            path = "/blocks/%s/children?page_size=100" % block_id
            if cursor:
                path += "&start_cursor=" + cursor
            r = self.request("GET", path)
            out.extend(r.get("results", []))
            if not r.get("has_more"):
                return out
            cursor = r.get("next_cursor")

    def set_status(self, page_id, prop_name, kind, option):
        return self.request("PATCH", "/pages/" + page_id, status_patch(prop_name, kind, option))
