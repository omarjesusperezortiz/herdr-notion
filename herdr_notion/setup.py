"""First-run setup helpers: find databases, pick properties, render config.toml.

Pure functions only; the screens live in tui.py.
"""
import json

from . import notion as nt

SEARCH_BODY = {"filter": {"property": "object", "value": "data_source"}, "page_size": 50}
COLUMN_TYPES = ("people", "select", "multi_select", "date", "unique_id")


def database_choices(search_response):
    """[{id, database_id, title}] from a /search response for data sources."""
    out = []
    for r in search_response.get("results", []):
        if r.get("object") != "data_source":
            continue
        out.append({
            "id": r["id"],
            "database_id": (r.get("parent") or {}).get("database_id", ""),
            "title": nt.rich_text(r.get("title")) or "(untitled)",
        })
    return out


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


def column_candidates(schema, status_prop, limit=2):
    props = schema.get("properties") or {}
    cols = [n for n, p in props.items() if p.get("type") in COLUMN_TYPES and n != status_prop]
    return cols[:limit]


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


def render_config(choice, schema, status_prop):
    """config.toml text for the picked data source."""
    cols = ([status_prop] if status_prop else []) + column_candidates(schema, status_prop)
    lines = [
        "# Written by the herdr-notion setup. See config.example.toml for every option.",
        "# The Notion token is stored next to this file, in `token` (owner-only).",
        "",
        "[notion]",
        "# %s" % choice["title"].replace("\n", " "),
        "database_id = %s" % toml_value(choice["database_id"]),
        "data_source_id = %s" % toml_value(choice["id"]),
        "status_property = %s" % toml_value(status_prop),
        "columns = %s" % toml_value(cols),
        "",
        "[[filters]]",
        'name = "All"',
    ]
    flt = open_filter(schema, status_prop) if status_prop else None
    if flt:
        lines += ["", "[[filters]]", 'name = "Open"', "filter = %s" % toml_value(flt)]
    return "\n".join(lines) + "\n"
