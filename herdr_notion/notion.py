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


def select_colors(schema):
    """{property: {option name: Notion color name}} for the select and multi-select properties that have colors."""
    out = {}
    for name, p in (schema.get("properties") or {}).items():
        if p.get("type") in ("select", "multi_select"):
            colors = {o["name"]: o["color"] for o in p[p["type"]].get("options", []) if o.get("color")}
            if colors:
                out[name] = colors
    return out


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


def short_stamp(iso):
    """'Oct 30, 09:00' (UTC) for an ISO-8601 timestamp; '' if unparsable."""
    from datetime import datetime
    try:
        t = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    if t.tzinfo is not None:
        t = t.astimezone()  # your own clock
    return "%s %d, %02d:%02d" % (t.strftime("%b"), t.day, t.hour, t.minute)


def checklist_progress(lines):
    """(done, total) of the to-do lines ([x] and [ ]) in a flattened body, or None when there are none."""
    marks = [CHECK.match(l.lstrip()).group(1) for l in lines if CHECK.match(l.lstrip())]
    return (sum(1 for m in marks if m in "xX"), len(marks)) if marks else None


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


def ticket_id_in(text):
    """The first ticket id (ET-2535) written in `text`, upper-cased, or ''."""
    m = re.search(r"(?<![A-Za-z0-9])([A-Za-z]{2,8}-\d{1,6})(?!\d)", text or "")
    return m.group(1).upper() if m else ""


def match_agent_to_pages(agent, pages):
    """The page an agent is on, from its title, name or branch (see match_agent), or None."""
    for page in pages:
        if match_agent(page, [agent]):
            return page
    return None


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


def fold(text):
    """Lower case without accents, for matching ('revisión' matches 'revision')."""
    return "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn")


def fuzzy_score(query, text):
    """How well `query` matches `text` (higher is better), or None. Every word of the query must match: a substring
    scores highest (more at the start of a word), otherwise its letters must appear in order."""
    t = fold(text)
    total = 0.0
    for tok in fold(query).split():
        i = t.find(tok)
        if i >= 0:
            total += 50 + (20 if i == 0 or not t[i - 1].isalnum() else 0)
            continue
        pos, prev, score = 0, -2, 0
        for ch in tok:
            j = t.find(ch, pos)
            if j < 0:
                return None
            score += (5 if j == prev + 1 else 1) + (3 if j == 0 or not t[j - 1].isalnum() else 0)
            prev, pos = j, j + 1
        total += score
    return total - len(t) * 0.05


def chip_value(text, limit=36):
    """A property value for a chip: timestamps shortened to the day, long text cut."""
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})T[\d:.]+Z?", text or "")
    return clip(m.group(1) if m else text, limit)


def elapsed_text(seconds):
    """'47s', '12m', '2h04m', '4d3h': how long something has run, in the shortest honest form."""
    s = max(0, int(seconds))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    if s < 86400:
        return "%dh%02dm" % (s // 3600, s % 3600 // 60)
    return "%dd%dh" % (s // 86400, s % 86400 // 3600)


def agent_state(status, done=False):
    """(glyph, color role, words) in herdr's own language: ◉ needs you, ◐ working, ● done, ✓ idle."""
    st = (status or "").lower()
    if done or st == "done":
        return "●", "green", "done"
    if any(w in st for w in ("block", "need", "wait", "input")):
        return "◉", "red", "needs you"
    if st == "working":
        return "◐", "amber", "working"
    if st == "idle":
        return "✓", "grey", "idle"
    return "○", "grey", "sent"


CHECK = re.compile(r"^(?:[-*]\s+)?\[( |x|X)\]\s*")  # "[ ] text", "- [x] text"
CALLOUT = "\x05"  # first character of a line that sits inside a callout box


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
        elif t.startswith(CALLOUT):
            out.append("callout")
        elif t.startswith("#"):
            out.append("heading")
        elif t == "---":
            out.append("rule")
        elif CHECK.match(t):
            out.append("done" if CHECK.match(t).group(1) in "xX" else "todo")
        elif t.startswith(">"):
            out.append("quote")
        else:
            out.append("plain")
    return out


def display_line(line, kind):
    """Text to draw for a styled body line (markers swapped for nicer glyphs)."""
    ind = line[:len(line) - len(line.lstrip())]
    t = line.lstrip()
    if kind == "callout":
        return t.lstrip(CALLOUT)
    if kind == "heading":
        return ind + t.lstrip("#").strip()
    if kind == "quote":
        return ind + "▎ " + t[1:].lstrip()
    if kind == "done":
        return ind + "☑ " + t[CHECK.match(t).end():].strip()
    if kind == "todo":
        return ind + "☐ " + t[CHECK.match(t).end():].strip()
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


# ---------------------------------------------------------------- inline styles
# A body line may carry toggles: BOLD, CODE, LINK and ITALIC switch a style on and off where they stand.
BOLD, CODE, LINK, ITALIC = "\x01", "\x02", "\x03", "\x04"
F_BOLD, F_CODE, F_LINK, F_ITALIC, F_BAR, F_DIM = 1, 2, 4, 8, 16, 32
_TOGGLE = {BOLD: F_BOLD, CODE: F_CODE, LINK: F_LINK, ITALIC: F_ITALIC}


def plain(line):
    """The line without its style toggles."""
    return re.sub("[\x01-\x05]", "", line)


def styled_chars(line):
    """[(char, flags)] of a line: toggles are consumed and set the flags of the characters after them."""
    out, flags = [], 0
    for ch in line:
        if ch in _TOGGLE:
            flags ^= _TOGGLE[ch]
        elif ch != CALLOUT:
            out.append((ch, flags))
    return out


def wrap_rich(line, width, hang="", first="", flags_first=0, flags_hang=0):
    """Word-wrap a line that carries toggles. Returns rows of runs [(text, flags)]; the first row starts with `first`, the
    next ones with `hang` (both plain). Words longer than the row are cut."""
    chars = styled_chars(line)
    rows, row, used = [], [], 0

    def start(prefix, f):
        return ([(prefix, f)] if prefix else []), cell_width(prefix)

    row, used = start(first, flags_first)
    word = []

    def put(cs):
        nonlocal row, used
        for ch, f in cs:
            if row and row[-1][1] == f:
                row[-1] = (row[-1][0] + ch, f)
            else:
                row.append((ch, f))
        used += sum(cell_width(c) for c, _ in cs)

    def newrow():
        nonlocal rows, row, used
        # trailing spaces never start a row nor end one
        while row and not row[-1][0].strip():
            row.pop()
        if row and not row[-1][0].strip(" "):
            row.pop()
        if row:
            row[-1] = (row[-1][0].rstrip(" "), row[-1][1])
        rows.append(row)
        row, used = start(hang, flags_hang)

    tokens, cur = [], []
    for ch, f in chars:
        if ch == " ":
            cur.append((ch, f))
            tokens.append(cur)
            cur = []
        else:
            if cur and cur[-1][0] == " ":
                tokens.append(cur)
                cur = []
            cur.append((ch, f))
    if cur:
        tokens.append(cur)
    room = max(4, width)
    for tok in tokens:
        w = sum(cell_width(c) for c, _ in tok)
        wtext = sum(cell_width(c) for c, _ in tok if c != " ")
        base = cell_width(hang)
        if used + wtext > width and used > base:
            newrow()
        while wtext > room - base and wtext > 0:  # a word wider than the row: cut it
            cut, acc = 0, 0
            for i, (c, _) in enumerate(tok):
                if acc + cell_width(c) > room - used:
                    break
                acc += cell_width(c)
                cut = i + 1
            if cut == 0:
                newrow()
                continue
            put(tok[:cut])
            tok = tok[cut:]
            newrow()
            wtext = sum(cell_width(c) for c, _ in tok if c != " ")
        put(tok)
    newrow() if row else None
    return rows or [[("", 0)]]
