"""Pure helpers for Notion pages: filters, properties, flattening, prompt rendering."""
import json
import re
import unicodedata

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
        titles = [x["title"] for x in v or [] if x.get("title")]
        return ", ".join(titles) if titles else ("%d linked" % len(v) if v else "")
    if t == "files":
        return ", ".join(f.get("name", "") for f in v or [])
    if t in ("created_by", "last_edited_by"):
        return (v or {}).get("name", "")
    return ""


TICKET_ID = re.compile(r"^[A-Za-z]{2,8}-\d+$")


def page_ticket_id(page):
    """The page's ticket id (like ET-2546): a unique_id property, or a text property holding just that shape."""
    for p in page.get("properties", {}).values():
        if p.get("type") in ("unique_id", "rich_text", "formula"):
            text = prop_text(p).strip()
            if TICKET_ID.match(text):
                return text
    return ""


def match_agent(page, agents):
    """The agent that works on this page, found from what herdr shows about it: its title, name or branch holds the
    ticket id (ET-2546) or the whole page title. None when nothing matches."""
    tid, title = page_ticket_id(page), page_title(page).lower()
    for a in agents:
        hay = " ".join((a.get("title", ""), a.get("name", ""), a.get("branch", ""))).lower()
        if tid and re.search(r"(?<![a-z0-9])%s(?!\d)" % re.escape(tid.lower()), hay):
            return a
        if len(title) >= 12 and title in hay:
            return a
    return None


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
    g = (group_name or "").lower().replace(" ", "_").replace("-", "_")
    if g in ("to_do", "todo"):
        return "todo"
    if g == "in_progress":
        return "progress"
    if g in ("complete", "done"):
        return "done"
    return ""


def guess_kind(option_name):
    """Marker kind for a select option with no group (plain select properties),
    guessed from its name: 'todo' | 'progress' | 'done' | ''."""
    n = (option_name or "").lower()
    if any(w in n for w in ("done", "finish", "complete", "closed", "shipped", "merged", "released")):
        return "done"
    if any(w in n for w in ("progress", "doing", "review", "started", "active", "wip", "testing")):
        return "progress"
    if any(w in n for w in ("to do", "todo", "backlog", "not started", "new", "open", "planned", "draft")):
        return "todo"
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


def checklist_progress(lines):
    """(done, total) of the to-do lines ([x] and [ ]) in a flattened body, or None when there are none."""
    marks = [l.lstrip()[:3] for l in lines if l.lstrip().startswith(("[x]", "[ ]"))]
    return (marks.count("[x]"), len(marks)) if marks else None


def page_relations(page):
    """[(property, page id, title)] for every linked page in the page's relation properties."""
    out = []
    for name, p in page.get("properties", {}).items():
        if p.get("type") == "relation":
            out += [(name, x.get("id", ""), x.get("title") or "") for x in p.get("relation") or []]
    return out


def relations_line(page, body=None):
    """'☑ 2/3 subtasks · ↗ 2 related' for the preview, or '' when there is neither."""
    parts = []
    prog = checklist_progress(body or [])
    if prog:
        parts.append("%s %d/%d subtasks" % ("☑" if prog[0] == prog[1] else "☐", prog[0], prog[1]))
    rel = page_relations(page)
    if rel:
        parts.append("↗ %d related (L)" % len(rel))
    return " · ".join(parts)


def parse_time(iso):
    """Aware datetime for an ISO-8601 timestamp or date, or None."""
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def edited_within(iso, seconds, now=None):
    from datetime import datetime, timezone
    t = parse_time(iso)
    return bool(t) and 0 <= ((now or datetime.now(timezone.utc)) - t).total_seconds() <= seconds


def style_lines(lines):
    """Classify flattened body lines for display: heading, rule, todo, done,
    quote, fence, code or plain. Tracks ``` fences across lines."""
    out, in_code = [], False
    for l in lines:
        t = l.lstrip()
        if t.startswith("```"):
            in_code = not in_code
            out.append("fence")
        elif in_code:
            out.append("code")
        elif t.startswith("#"):
            out.append("heading")
        elif t == "---":
            out.append("rule")
        elif t.startswith("[x]"):
            out.append("done")
        elif t.startswith("[ ]"):
            out.append("todo")
        elif t.startswith(">"):
            out.append("quote")
        else:
            out.append("plain")
    return out


def display_line(line, kind):
    """Text to draw for a styled body line (markers swapped for nicer glyphs)."""
    ind = line[:len(line) - len(line.lstrip())]
    t = line.lstrip()
    if kind == "heading":
        return ind + t.lstrip("#").strip()
    if kind == "quote":
        return ind + "▎ " + t[1:].lstrip()
    if kind == "done":
        return ind + "☑ " + t[3:].strip()
    if kind == "todo":
        return ind + "☐ " + t[3:].strip()
    if kind == "rule":
        return ind + "─" * 20
    if t.startswith("- "):
        return ind + "• " + t[2:]
    return line


def clean(text):
    """One line of text: newlines, tabs and runs of spaces become single spaces."""
    return " ".join((text or "").split())


def cell_width(text):
    """Terminal cells used by `text` (wide CJK/emoji count 2, combining marks 0)."""
    n = 0
    for ch in text:
        if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def clip(text, width):
    """Cut `text` to at most `width` cells, ending with '…' when shortened."""
    text = clean(text)
    if cell_width(text) <= width:
        return text
    out, used = "", 0
    for ch in text:
        w = cell_width(ch)
        if used + w > width - 1:
            break
        out, used = out + ch, used + w
    return out + "…" if width > 0 else ""


def pad(text, width):
    """Clip to `width` cells and pad with spaces up to exactly `width` cells."""
    text = clip(text, width)
    return text + " " * (width - cell_width(text))


def fit_hint_parts(hints, width):
    """[(index into hints, text)] that fit `width` without cutting a hint.

    Hints are in priority order. Each gets its label while it fits; the rest
    fall back to the bare key, and are dropped only if even that does not fit.
    """
    parts = []
    used = 0
    for i, (k, label) in enumerate(hints):
        full = "%s %s" % (k, label)
        rest = sum(len(kk) + 2 for kk, _ in hints[i + 1:])  # reserve room for bare keys
        if used + len(full) + rest <= width:
            parts.append((i, full))
            used += len(full) + 2
        elif used + len(k) <= width:
            parts.append((i, k))
            used += len(k) + 2
    return parts


def fit_hints(hints, width):
    """Join (key, label) hints into one line that fits `width` (see fit_hint_parts)."""
    return "  ".join(t for _, t in fit_hint_parts(hints, width))


def clipboard_cmd(which):
    """argv of the first available clipboard tool, or None. `which` is shutil.which."""
    for argv in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]):
        if which(argv[0]):
            return argv
    return None


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
