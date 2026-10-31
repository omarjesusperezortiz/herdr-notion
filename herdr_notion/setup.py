"""First-run setup helpers: find databases, pick properties, render config.toml.

Pure functions only; the screens live in tui.py.
"""
import json

import re
from . import notion as nt

COLUMN_TYPES = ("people", "select", "multi_select", "date", "unique_id")


STATUS_WORDS = ("status", "state", "stage")


def status_candidates(schema):
    """Names of status/select properties, best guess first: a property named
    Status/State/Stage, then Notion's status type, then names containing those
    words (e.g. 'PR Status'), then any other select."""
    props = schema.get("properties") or {}

    def rank(item):
        name, p = item
        low = name.lower().strip()
        if low in STATUS_WORDS:
            return 0
        if p.get("type") == "status":
            return 1
        if any(w in low for w in STATUS_WORDS):
            return 2
        return 3

    cands = [(n, p) for n, p in props.items() if p.get("type") in ("status", "select")]
    return [n for n, _ in sorted(cands, key=rank)]  # sorted() is stable


def property_label(schema, name):
    """'Status  (select: To Do, Blocked, …)' so the user can tell candidates apart."""
    p = (schema.get("properties") or {}).get(name) or {}
    kind = p.get("type", "")
    opts = [o["name"] for o in (p.get(kind) or {}).get("options", [])]
    shown = ", ".join(opts[:4]) + (", …" if len(opts) > 4 else "")
    return "%s  (%s: %s)" % (name, kind, shown)


SKIP_WORDS = ("completed", "created", "updated", "edited", "notion user", "last ")


def column_candidates(schema, status_prop, limit=2):
    """Columns worth showing next to the status: who it is assigned to, then priority, then a due date."""
    props = schema.get("properties") or {}

    def rank(name, p):
        low = name.lower()
        if any(w in low for w in SKIP_WORDS):
            return None
        if any(w in low for w in PERSON_WORDS) and p.get("type") in ("people", "relation"):
            return 0
        if "priorit" in low and p.get("type") in ("select", "status"):
            return 1
        if p.get("type") == "date" and any(w in low for w in ("due", "deadline", "fecha")):
            return 2
        return 3 if p.get("type") in COLUMN_TYPES else None

    ranked = [(rank(n, p), i, n) for i, (n, p) in enumerate(props.items()) if n != status_prop]
    return [n for r, i, n in sorted(x for x in ranked if x[0] is not None)][:limit]


def open_filter(schema, status_prop):
    """Filter for 'not finished' pages. A Notion status property uses its
    'Complete' group; a plain select has no groups, so options named like
    done/closed/shipped count as finished. None when nothing is finished."""
    p = (schema.get("properties") or {}).get(status_prop) or {}
    kind = p.get("type")
    if kind not in ("status", "select"):
        return None
    names = [o["name"] for o in p[kind].get("options", [])]
    if kind == "status":
        done = [n for n, g in nt.status_groups(schema, status_prop).items()
                if nt.group_kind(g) == "done" and n in names]
    else:
        done = [n for n in names if nt.guess_kind(n) == "done"]
    if not done:
        return None
    conds = [{"property": status_prop, kind: {"does_not_equal": n}} for n in done]
    return conds[0] if len(conds) == 1 else {"and": conds}


PERSON_WORDS = ("assign", "owner", "responsible", "responsable", "asignad", "person", "who")
SPRINT_RE = re.compile(r"(current|this|active|actual|activo)\W*sprint|sprint\W*(actual|activo|en curso|current)", re.I)


def person_property(schema):
    """The people property that says who a page belongs to, best name match first."""
    props = [(n, p) for n, p in (schema.get("properties") or {}).items() if p.get("type") == "people"]
    props.sort(key=lambda np: 0 if any(w in np[0].lower() for w in PERSON_WORDS) else 1)
    return props[0][0] if props else ""


def sprint_filter(schema):
    """Filter for 'in the current sprint' when the database has a checkbox or formula named like it."""
    for n, p in (schema.get("properties") or {}).items():
        if SPRINT_RE.search(n) and p.get("type") in ("checkbox", "formula"):
            if p["type"] == "checkbox":
                return {"property": n, "checkbox": {"equals": True}}
            return {"property": n, "formula": {"checkbox": {"equals": True}}}
    return None


def assignee_relation(schema):
    """The relation property that says who a page belongs to (Assignee, Owner…), when there is one."""
    props = [(n, p) for n, p in (schema.get("properties") or {}).items() if p.get("type") == "relation"
             and any(w in n.lower() for w in PERSON_WORDS)]
    return props[0][0] if props else ""


def suggest_filters(schema, status_prop, views=None, me_page=None):
    """Tabs worth having, first one is what opens: Mine, Current sprint, Open, All. Only what this database supports."""
    open_ = open_filter(schema, status_prop) if status_prop else None

    def and_(*conds):
        conds = [c for c in conds if c]
        return conds[0] if len(conds) == 1 else {"and": conds}

    out = []
    who = person_property(schema)
    mine = None
    if me_page:  # same field as the boards in Notion: the Assignee relation to the person's own page
        mine = {"property": me_page[0], "relation": {"contains": me_page[1]}}
    elif who:
        mine = {"property": who, "people": {"contains": "me"}}
    if mine:
        out.append({"name": "Mine", "filter": and_(mine, open_)})
    sprint = sprint_filter(schema)
    if sprint:  # yours when we know who you are, the team's otherwise
        out.append({"name": "Sprint", "filter": and_(mine, sprint, open_) if mine else and_(sprint, open_)})
    if open_:
        out.append({"name": "Open", "filter": open_})
    out.append({"name": "All"})
    return out  # the saved views of the database are one key away (V), not more tabs


def render_filters(filters):
    lines = []
    for f in filters:
        lines += ["[[filters]]", "name = %s" % toml_value(f["name"])]
        if f.get("view"):
            lines.append("view = %s" % toml_value(f["view"]))
        if f.get("filter"):
            lines.append("filter = %s" % toml_value(f["filter"]))
        if f.get("sort"):
            lines.append("sort = %s" % toml_value(f["sort"]))
        lines.append("")
    return "\n".join(lines)


def replace_filters(text, filters):
    """config.toml text with every [[filters]] table replaced by `filters`; the rest is kept as written."""
    text = re.sub(r"(?ms)^\[\[filters\]\]\n.*?(?=^\[|\Z)", "", text).rstrip()
    return text + "\n\n" + render_filters(filters)


def toml_value(v):
    """Render str / list / dict / bool / int as TOML (inline tables for dicts)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # JSON strings are valid TOML basic strings
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join("%s = %s" % (json.dumps(k), toml_value(x)) for k, x in v.items()) + " }"
    raise TypeError(type(v))


def render_config(choice, schema, status_prop, filters=None):
    """config.toml text for the picked data source."""
    cols = ([status_prop] if status_prop else []) + column_candidates(schema, status_prop)
    lines = [
        "# Written by the herdr-notion setup. See config.example.toml for every option.",
        "# Your Notion sign-in is stored next to this file, in `oauth.json` (owner-only).",
        "",
        "[notion]",
        "# %s" % choice["title"].replace("\n", " "),
        "database_id = %s" % toml_value(choice["database_id"]),
        "data_source_id = %s" % toml_value(choice["id"]),
        "status_property = %s" % toml_value(status_prop),
        "columns = %s" % toml_value(cols),
        "",
    ]
    return "\n".join(lines) + "\n" + render_filters(filters or suggest_filters(schema, status_prop))
