"""Notion data access through the hosted MCP server, shaped like the REST API objects the
rest of the plugin already understands (schema, pages, blocks as plain-text lines)."""
import json
import re

from . import mcp, notion as nt, oauth


def uid(ref):
    """Bare user id from a `user://<id>` reference."""
    return ref[len("user://"):] if isinstance(ref, str) and ref.startswith("user://") else ref


def person_ids(rows, schema):
    """Every user id referenced by a people property in `rows`."""
    kinds = [n for n, p in schema["properties"].items() if p["type"] == "people"]
    out = set()
    for row in rows:
        for n in kinds:
            val = row.get(n) or row.get("userDefined:" + n)
            if val:
                out.update(uid(i) for i in (val if isinstance(val, list) else json.loads(val)))
    return out


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
        elif t == "relation":
            props[name] = {"type": t, t: {"data_source": collection_id(p.get("dataSourceUrl", ""))}}
        else:
            props[name] = {"type": t, t: {}}
    return {"title": [{"plain_text": state.get("name", "")}], "properties": props, "url": state.get("url", "")}


def to_page(row, schema, users=None, titles=None):
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
            props[name] = {"type": t, t: [{"id": uid(i), "name": names.get(uid(i), "(unknown user)")} for i in ids]}
        elif t == "relation":
            ids = val if isinstance(val, list) else json.loads(val or "[]")
            props[name] = {"type": t, t: [{"id": i, "title": (titles or {}).get(i.split("?")[0], "")} for i in ids]}
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
    if cond == "relation":
        return dict(base, propertyType="relation", operator={"contains": "relation_contains", "does_not_contain": "relation_does_not_contain"}[op],
                    value=_exact(val))
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
        self._titles = {}
        self.want = set()  # relation columns shown in the list: their target pages' titles get looked up

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

    def resolve_users(self, ids):
        """Fill the id -> name cache for ids not seen yet (a few parallel lookups)."""
        todo = [i for i in ids if i not in self._users][:40]
        if not todo:
            return

        def one(i):
            try:
                res = self.call("notion-get-users", {"user_id": i})
                for u in res.get("results", []) if isinstance(res, dict) else []:
                    if u.get("id") == i and u.get("name"):
                        return i, u["name"]
            except nt.NotionError:
                pass
            return i, None

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as pool:
            for i, name in pool.map(one, todo):
                self._users[i] = name or "(unknown user)"

    def relation_urls(self, rows, schema):
        out = set()
        for row in rows:
            for n in self.want:
                if schema["properties"].get(n, {}).get("type") == "relation":
                    val = row.get(n) or row.get("userDefined:" + n)
                    if val:
                        out.update(u.split("?")[0] for u in (val if isinstance(val, list) else json.loads(val)))
        return out

    def resolve_titles(self, urls):
        """Fill the url -> page title cache (a few parallel fetches)."""
        todo = [u for u in urls if u not in self._titles][:40]
        if not todo:
            return

        def one(u):
            try:
                res = self.call("notion-fetch", {"id": u})
                return u, unescape((res.get("title") if isinstance(res, dict) else "") or "")
            except nt.NotionError:
                return u, ""

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as pool:
            for u, title in pool.map(one, todo):
                self._titles[u] = title

    def database_url(self):
        """URL of the database that owns this data source (read from the ancestors of any one page)."""
        if getattr(self, "_db_url", ""):
            return self._db_url
        rows = self.call("notion-query-data-sources", {"data": {"mode": "rows", "data_source_url": "collection://" + self.data_source_id, "limit": 1}})
        rows = rows.get("results", []) if isinstance(rows, dict) else []
        if not rows:
            raise nt.NotionError("the database has no pages to read its views from")
        text = self._fetch_page(rows[0]["url"])
        m = re.search(r'<ancestor-2-database url="([^"]+)"', text) or re.search(r'<parent-database url="([^"]+)"', text)
        if not m:
            raise nt.NotionError("could not find the database of this data source")
        self._db_url = m.group(1)
        return self._db_url

    def views(self):
        """[{name, type, url}] for the saved views of the database; `url` can be given to a view query."""
        if getattr(self, "_views", None) is not None:
            return self._views
        dburl = self.database_url()
        res = self.call("notion-fetch", {"id": dburl})
        text = res.get("text", "") if isinstance(res, dict) else str(res)
        dbid = re.sub(r"\W", "", dburl.split("?")[0].rstrip("/").split("/")[-1].split("-")[-1]) if "/p/" not in dburl else re.sub(r"\W", "", dburl.split("/p/")[-1].split("?")[0])
        out = []
        for vurl, js in re.findall(r'<view url="\{\{(view://[^}]+)\}\}">\s*(\{.*?\})\s*</view>', text, re.S):
            try:
                d = json.loads(js)
            except ValueError:
                continue
            out.append({"name": d.get("name") or "View", "type": d.get("type", ""),
                        "url": "https://www.notion.so/%s?v=%s" % (dbid, vurl.split("view://")[1].replace("-", ""))})
        self._views = out
        return out

    def query_view(self, body, cursor=None):
        """One page of a saved view, in the order and with the filters the view has in Notion (real cursor paging)."""
        schema = self.discover()
        data = {"mode": "view", "view_url": body["view_url"], "page_size": max(1, min(int(body.get("page_size", 50)), 100))}
        if cursor:
            data["start_cursor"] = cursor
        res = self.call("notion-query-data-sources", {"data": data})
        rows = res.get("results", []) if isinstance(res, dict) else []
        self.resolve_users(person_ids(rows, schema))
        self.resolve_titles(self.relation_urls(rows, schema))
        more = bool(isinstance(res, dict) and res.get("has_more") and res.get("next_cursor"))
        return {"results": [to_page(r, schema, self._users, self._titles) for r in rows], "has_more": more,
                "cursor": res.get("next_cursor") if more else None, "note": ""}

    def find_person_page(self, prop, name):
        """URL of the page in the relation's target database titled like `name` (the signed-in person), or ''."""
        schema = self.discover()
        target = (schema["properties"].get(prop, {}).get("relation") or {}).get("data_source", "")
        if not target or not name:
            return ""
        tokens = lambda t: set(self.fold_name(t).replace("-", " ").split())
        mine, best = tokens(name), ("", 0)
        for query in (name, name.split()[0]):
            res = self.call("notion-search", {"query": query, "data_source_url": "collection://" + target, "page_size": 10, "max_highlight_length": 0})
            for r in res.get("results", []) if isinstance(res, dict) else []:
                theirs = tokens(r.get("title", ""))
                # the person's page is titled with their name, maybe a shorter form of it ("Omar Perez" for "Omar Perez Ortiz")
                if r.get("type") == "page" and len(theirs) >= 2 and theirs <= mine and len(theirs) > best[1]:
                    best = (r.get("url", "").split("?")[0], len(theirs))
            if best[0]:
                break
        return best[0]

    @staticmethod
    def fold_name(t):
        import unicodedata
        return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower().strip()) if unicodedata.category(c) != "Mn")

    def paging_key(self, schema):
        """Property that orders pages for paging: last edited, else created."""
        for kind in ("last_edited_time", "created_time"):
            for n, p in schema["properties"].items():
                if p["type"] == kind:
                    return n, kind
        return "", ""

    def query(self, body, cursor=None, seen=()):
        """One page of rows, newest edit first. The rows call has no cursor and date filters compare whole days, so
        the next page continues from a day: rows on or before the last day seen (overlapping that day, which the
        caller de-duplicates through `seen`). When a single day holds more rows than one page, the other end of
        that day is read in ascending order and then the walk moves on to earlier days; `note` is set when rows of
        such a day may be missing. `cursor` is opaque: pass back what the previous call returned."""
        if body.get("view_url"):
            return self.query_view(body, cursor)
        schema = self.discover()
        key, kind = self.paging_key(schema)
        user_sorted = bool(body.get("sorts"))
        paged = bool(key) and not user_sorted
        cursor = cursor or {}
        mode, day = cursor.get("mode", "start"), cursor.get("day", "")
        limit = 100 if (cursor or not paged) else max(1, min(int(body.get("page_size", 50)), 100))
        data = {"mode": "rows", "data_source_url": "collection://" + self.data_source_id, "limit": limit}
        conds = [to_mcp_filter(body["filter"], schema)] if body.get("filter") else []
        op = {"desc": "date_is_on_or_before", "asc": "date_is", "before": "date_is_before"}.get(mode)
        if op and paged:
            conds.append({"type": "property", "property": key, "propertyType": kind, "operator": op,
                          "value": {"type": "exact", "value": day}})
        if conds:
            data["filter"] = {"type": "group", "operator": "and", "filters": conds}
        sorts = []
        for sd in body.get("sorts") or []:
            prop = sd.get("property") or next((n for n, p in schema["properties"].items() if p["type"] == sd.get("timestamp")), "")
            if prop:
                sorts.append({"property": prop, "direction": sd.get("direction", "descending")})
        if paged:
            sorts = [{"property": key, "direction": "ascending" if mode == "asc" else "descending"}]
        if sorts:
            data["sort"] = sorts
        res = self.call("notion-query-data-sources", {"data": data})
        rows = res.get("results", []) if isinstance(res, dict) else []
        self.resolve_users(person_ids(rows, schema))
        self.resolve_titles(self.relation_urls(rows, schema))
        pages = [to_page(r, schema, self._users, self._titles) for r in rows]
        out = {"results": pages, "has_more": False, "cursor": None, "note": ""}
        if not paged:
            return out
        fresh = [p for p in pages if p["id"] not in seen]
        last_day = (rows[-1].get(key, "") if rows else "")[:10]
        if mode in ("start", "desc"):
            if rows and (mode == "start" or fresh):
                out.update(has_more=len(rows) >= limit, cursor={"mode": "desc", "day": last_day})
            elif rows:  # a whole page of rows already seen: this day has more rows than one page
                out.update(has_more=True, cursor={"mode": "asc", "day": day})
        elif mode == "asc":
            out.update(has_more=True, cursor={"mode": "before", "day": day})
            if len(rows) >= limit and len(fresh) == len(rows):  # no overlap with what we have: the day is not covered
                out["note"] = "more than %d pages were edited on %s; some of them are not listed" % (2 * limit, day)
        else:  # "before": earlier days, normal walk again
            if rows:
                out.update(has_more=len(rows) >= limit, cursor={"mode": "desc", "day": last_day})
        return out

    def _fetch_page(self, page_id):
        res = self.call("notion-fetch", {"id": page_id})
        return res.get("text", "") if isinstance(res, dict) else str(res)

    def get_page(self, page_id):
        text = self._fetch_page(page_id)
        row = tag_json(text, "properties")
        row.setdefault("url", page_id)
        self.resolve_users(person_ids([row], self.discover()))
        self.resolve_titles(self.relation_urls([row], self.discover()))
        return to_page(row, self.discover(), self._users, self._titles)

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
