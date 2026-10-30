"""Notion data access through the hosted MCP server, shaped like the REST API objects the
rest of the plugin already understands (schema, pages, blocks as plain-text lines)."""
import json
import re

from . import mcp, notion as nt, oauth


def unescape(s):
    return re.sub(r"\\([\\\[\](){}*_`#~>|+.!-])", r"\1", s or "")


def collection_id(text):
    m = re.search(r"collection://([0-9a-fA-F-]{36})", text or "")
    return m.group(1) if m else ""


def tag_json(text, tag):
    m = re.search(r"<%s>\s*(\{.*?\})\s*</%s>" % (tag, tag), text or "", re.S)
    if not m:
        raise nt.NotionError("unexpected reply from Notion (no <%s>)" % tag)
    return json.loads(m.group(1))


GROUP_NAMES = {"to_do": "To-do", "in_progress": "In progress", "complete": "Complete"}
TYPE_MAP = {"person": "people", "text": "rich_text", "auto_increment_id": "rich_text"}


def schema_from_state(state):
    """REST-shaped schema from the `<data-source-state>` JSON of a fetched data source."""
    props = {}
    for p in (state.get("schema") or {}).values():
        t = TYPE_MAP.get(p.get("type"), p.get("type"))
        name = p.get("name")
        if t == "select" or t == "multi_select":
            props[name] = {"type": t, t: {"options": [{"id": o["name"], "name": o["name"]} for o in p.get("options", [])]}}
        elif t == "status":
            opts, groups = [], []
            for key, items in (p.get("groups") or {}).items():
                names = [o["name"] for o in items]
                opts += [{"id": n, "name": n} for n in names]
                if names:
                    groups.append({"name": GROUP_NAMES.get(key, key), "option_ids": names})
            props[name] = {"type": "status", "status": {"options": opts, "groups": groups}}
        else:
            props[name] = {"type": t, t: {}}
    return {"title": [{"plain_text": state.get("name", "")}], "properties": props, "url": state.get("url", "")}


def to_page(row, schema, users=None):
    """REST-shaped page from a rows-mode result (or the <properties> JSON of a fetched page)."""
    kinds = {n: p["type"] for n, p in schema["properties"].items()}
    props = {}
    for key, val in row.items():
        name = key[len("userDefined:"):] if key.startswith("userDefined:") else key
        if name.startswith("date:"):
            continue
        t = kinds.get(name)
        if t is None or (isinstance(val, str) and "Result://" in val):
            continue
        if t == "title":
            props[name] = {"type": t, t: [{"plain_text": unescape(val)}]}
        elif t in ("select", "status"):
            props[name] = {"type": t, t: {"name": val} if val else None}
        elif t == "multi_select":
            items = val if isinstance(val, list) else json.loads(val or "[]")
            props[name] = {"type": t, t: [{"name": x} for x in items]}
        elif t == "people":
            ids = val if isinstance(val, list) else json.loads(val or "[]")
            names = users or {}
            props[name] = {"type": t, t: [{"id": i, "name": names.get(i, "someone")} for i in ids]}
        elif t == "relation":
            ids = val if isinstance(val, list) else json.loads(val or "[]")
            props[name] = {"type": t, t: [{"id": i} for i in ids]}
        elif t == "checkbox":
            props[name] = {"type": t, t: val in (1, True, "__YES__")}
        elif t in ("number", "url", "email", "phone_number", "created_time", "last_edited_time"):
            props[name] = {"type": t, t: val if val != "" else None}
        elif t in ("rich_text",):
            props[name] = {"type": t, t: [{"plain_text": unescape(str(val))}]}
    for name, t in kinds.items():
        if t == "date" and row.get("date:%s:start" % name):
            props[name] = {"type": "date", "date": {"start": row["date:%s:start" % name], "end": row.get("date:%s:end" % name) or None}}
    edited = next((row[n] for n, t in kinds.items() if t == "last_edited_time" and n in row), "")
    return {"id": row.get("url", ""), "url": (row.get("url") or "").split("?")[0], "last_edited_time": edited, "properties": props}


# ---------------------------------------------------------------- filters (REST shape -> MCP shape)
def _exact(v):
    return {"type": "exact", "value": v}


def to_mcp_filter(f, schema):
    if "and" in f or "or" in f:
        op = "and" if "and" in f else "or"
        return {"type": "group", "operator": op, "filters": [to_mcp_filter(x, schema) for x in f[op]]}
    prop = f["property"]
    kind = schema["properties"].get(prop, {}).get("type")
    cond = next((k for k in f if k != "property"), None)
    if cond is None or kind is None:
        raise nt.NotionError("filter on unknown property %r" % prop)
    op, val = next(iter(f[cond].items()))
    base = {"type": "property", "property": prop}
    if op in ("is_empty", "is_not_empty"):
        return dict(base, propertyType=kind, operator=op)
    if cond == "status":
        return dict(base, propertyType="status", operator={"equals": "status_is", "does_not_equal": "status_is_not"}[op],
                    value={"type": "is_option", "value": val})
    if cond == "select":
        return dict(base, propertyType="select", operator={"equals": "enum_is", "does_not_equal": "enum_is_not"}[op], value=_exact(val))
    if cond == "multi_select":
        return dict(base, propertyType="multi_select", operator={"contains": "enum_contains", "does_not_contain": "enum_does_not_contain"}[op], value=_exact(val))
    if cond in ("title", "rich_text"):
        ops = {"equals": "string_is", "does_not_equal": "string_is_not", "contains": "string_contains",
               "does_not_contain": "string_does_not_contain", "starts_with": "string_starts_with", "ends_with": "string_ends_with"}
        return dict(base, propertyType="title" if cond == "title" else "text", operator=ops[op], value=_exact(val))
    if cond == "people":
        value = {"type": "relative", "value": "me"} if val == "me" else _exact("user://%s" % val)
        return dict(base, propertyType="person", operator={"contains": "person_contains", "does_not_contain": "person_does_not_contain"}[op], value=value)
    if cond == "checkbox":
        return dict(base, propertyType="checkbox", operator="checkbox_is", value={"type": "exact", "value": bool(val)})
    if cond == "formula":  # {"formula": {"checkbox": {"equals": true}}}
        rtype, rcond = next(iter(f["formula"].items()))
        inner = to_mcp_filter({"property": prop, rtype: rcond}, {"properties": {prop: {"type": rtype}}})
        return dict(base, propertyType="formula", operator="any", resultFilter=inner)
    if cond == "number":
        ops = {"equals": "number_equals", "does_not_equal": "number_does_not_equal", "greater_than": "number_greater_than",
               "less_than": "number_less_than", "greater_than_or_equal_to": "number_greater_than_or_equal_to",
               "less_than_or_equal_to": "number_less_than_or_equal_to"}
        return dict(base, propertyType="number", operator=ops[op], value={"type": "exact", "value": val})
    if cond == "date":
        ops = {"equals": "date_is", "before": "date_is_before", "after": "date_is_after",
               "on_or_before": "date_is_on_or_before", "on_or_after": "date_is_on_or_after"}
        return dict(base, propertyType="date", operator=ops[op], value=_exact(val))
    raise nt.NotionError("filter %s.%s is not supported with the browser sign-in" % (cond, op))


class McpClient:
    def __init__(self, data_source_id="", mcp_client=None):
        self.mcp = mcp_client or mcp.Mcp()
        self.data_source_id = data_source_id
        self._schema = None
        self._users = {}

    # --- plumbing
    def call(self, name, args=None):
        try:
            return self.mcp.call(name, args)
        except mcp.McpError:
            raise
        except oauth.OAuthError as e:
            raise nt.NotionError(str(e))

    def whoami(self):
        res = self.call("notion-get-self", {})
        ws = (res.get("workspace") or {}).get("name") if isinstance(res, dict) else ""
        user = (res.get("user") or {}).get("name") if isinstance(res, dict) else ""
        return {"workspace": ws or "", "user": user or ""}

    # --- databases
    def search_databases(self, term):
        """[{id (data source), title}] for databases matching a name, or the one a Notion URL points to."""
        term = term.strip()
        if re.match(r"https?://", term):
            return [self.database_choice(term)]
        res = self.call("notion-search", {"query": term, "page_size": 25, "max_highlight_length": 0})
        out = []
        for r in res.get("results", []) if isinstance(res, dict) else []:
            if r.get("type") == "database":
                out.append({"title": r.get("title") or "(untitled)", "url": r["url"]})
            elif r.get("parent_data_source_url") and not any(o.get("ds") == r["parent_data_source_url"] for o in out):
                pass
        return out

    def database_choice(self, url):
        res = self.call("notion-fetch", {"id": url})
        text = res.get("text", "") if isinstance(res, dict) else str(res)
        cid = collection_id(text)
        if not cid:
            raise nt.NotionError("that Notion link is not a database")
        return {"id": cid, "database_id": "", "title": (res.get("title") if isinstance(res, dict) else "") or "(untitled)"}

    # --- schema, rows, pages
    def discover(self):
        if self._schema:
            return self._schema
        if not self.data_source_id:
            raise nt.NotionError("no database configured")
        res = self.call("notion-fetch", {"id": "collection://" + self.data_source_id})
        text = res.get("text", "") if isinstance(res, dict) else str(res)
        self._schema = schema_from_state(tag_json(text, "data-source-state"))
        return self._schema

    def query(self, body):
        schema = self.discover()
        data = {"mode": "rows", "data_source_url": "collection://" + self.data_source_id,
                "limit": max(1, min(int(body.get("page_size", 50)), 100))}
        if body.get("filter"):
            data["filter"] = to_mcp_filter(body["filter"], schema)
            if data["filter"]["type"] != "group":
                data["filter"] = {"type": "group", "operator": "and", "filters": [data["filter"]]}
        sorts = []
        for s in body.get("sorts") or []:
            prop = s.get("property") or next((n for n, p in schema["properties"].items() if p["type"] == s.get("timestamp")), "")
            if prop:
                sorts.append({"property": prop, "direction": s.get("direction", "descending")})
        if sorts:
            data["sort"] = sorts
        res = self.call("notion-query-data-sources", {"data": data})
        rows = res.get("results", []) if isinstance(res, dict) else []
        return {"results": [to_page(r, schema, self._users) for r in rows], "has_more": bool(isinstance(res, dict) and res.get("has_more"))}

    def _fetch_page(self, page_id):
        res = self.call("notion-fetch", {"id": page_id})
        return res.get("text", "") if isinstance(res, dict) else str(res)

    def get_page(self, page_id):
        text = self._fetch_page(page_id)
        row = tag_json(text, "properties")
        row.setdefault("url", page_id)
        return to_page(row, self.discover(), self._users)

    def body_lines(self, page_id):
        text = self._fetch_page(page_id)
        m = re.search(r"<content>\n?(.*?)\n?</content>", text, re.S)
        return markdown_lines(m.group(1) if m else "")

    def set_status(self, page_id, prop, kind, option):
        self.call("notion-update-page", {"page_id": page_id, "command": "update_properties", "properties": {prop: option}})
        return self.get_page(page_id)


def markdown_lines(md):
    """Plain-text lines from Notion-flavoured markdown (tags and links stripped)."""
    md = re.sub(r"<br\s*/?>", "\n", md)
    md = re.sub(r"<mention[^>]*/>", "(link)", md)
    md = re.sub(r"</?(callout|code|b|i|u|s|span|details|summary|columns?|toggle|table|tr|td|th|colgroup|col)[^>]*>", "", md)
    md = re.sub(r"!?\[([^\]]*)\]\(([^)]*)\)", lambda m: m.group(1) or m.group(2), md)
    lines = [unescape(l.rstrip()) for l in md.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    out = []
    for l in lines:
        if l or (out and out[-1]):
            out.append(l)
    return out
