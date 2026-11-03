"""The new-ticket form: which fields it offers, what the tab you are on already decides, and the property values Notion's
create call wants. No screen code here; the box itself is drawn by tui.py."""
import re

# what a field can be, and how a value of that kind is chosen
PICK = ("status", "select", "relation")
TYPED = ("title", "rich_text", "number", "url", "date", "email", "phone_number")
SUPPORTED = PICK + TYPED
# the fields offered first, when the config does not say (names are matched without case, first one that exists wins)
PREFERRED = [("Status",), ("Assignee",), ("Project",), ("Type",), ("Priority",), ("Sprints", "Sprint"), ("Due", "Due date"), ("SPs", "Story points")]
UNSET = {"text": "", "raw": None, "src": ""}


def field(name, kind, text="", raw=None, src=""):
    return {"name": name, "kind": kind, "text": text, "raw": raw, "src": src}


def default_fields(schema, status_property="", wanted=None):
    """The fields of the form, in order: the title first, then `wanted` (the `new_fields` of the config) or the usual ones
    this database has. Properties Notion computes (formulas, rollups, dates it sets) are never offered."""
    props = schema.get("properties", {})
    title = next((n for n, p in props.items() if p["type"] == "title"), "Name")
    by_fold = {n.lower(): n for n in props}
    names = []
    if wanted:
        names = [by_fold[w.lower()] for w in wanted if w.lower() in by_fold]
    else:
        for group in PREFERRED:
            for cand in ((status_property,) if group == ("Status",) and status_property else group):
                if cand.lower() in by_fold:
                    names.append(by_fold[cand.lower()])
                    break
    out = [field(title, "title")]
    for n in names:
        kind = props[n]["type"]
        if n != title and kind in SUPPORTED and kind != "title" and not any(f["name"] == n for f in out):
            out.append(field(n, kind))
    return out


def leaves(flt):
    """The single conditions of a Notion filter, whatever the nesting of and / or."""
    if not isinstance(flt, dict):
        return []
    for key in ("and", "or"):
        if key in flt:
            return [leaf for sub in flt[key] for leaf in leaves(sub)]
    return [flt] if "property" in flt else []


def cond_of(leaf):
    kind = next((k for k in leaf if k != "property"), "")
    return kind, (leaf.get(kind) if isinstance(leaf.get(kind), dict) else {})


def tab_fills(flt, fields, titles=None):
    """{field name: {"text", "raw", "src"}} the tab you are on already decides: one that keeps what is linked to a page (yours)
    gives the ticket that link; one that keeps a status or select option gives that value."""
    titles = titles or {}
    names = {f["name"] for f in fields}
    out = {}
    for leaf in leaves(flt):
        name = leaf["property"]
        kind, cond = cond_of(leaf)
        if name not in names:
            continue
        if kind == "relation" and "contains" in cond:
            url = cond["contains"].split("?")[0]
            out[name] = {"text": titles.get(url) or "this tab's page", "raw": [url], "src": "tab"}
        elif kind in ("select", "status") and "equals" in cond:
            out[name] = {"text": cond["equals"], "raw": cond["equals"], "src": "tab"}
    return out


def still_listed(flt, fields):
    """Would a ticket made with these fields be on that tab after a refresh? False when it is not, or when a condition
    cannot be judged here (a formula such as 'current sprint', a person known only to Notion)."""
    by_name = {f["name"]: f for f in fields}
    for leaf in leaves(flt):
        f = by_name.get(leaf["property"])
        raw = f["raw"] if f else None
        kind, cond = cond_of(leaf)
        if kind == "relation" and "contains" in cond:
            ok = bool(raw) and cond["contains"].split("?")[0] in raw
        elif kind in ("select", "status") and "equals" in cond:
            ok = raw == cond["equals"]
        elif kind in ("select", "status") and "does_not_equal" in cond:
            ok = raw != cond["does_not_equal"]
        else:
            ok = False
        if not ok:
            return False
    return True


def property_values(fields, schema, body=""):
    """({property name: value for Notion's create call}, the body): select and status by option name, a relation as a list
    of page links, a number as a number, a date as the split `date:` keys, a property called id or url with userDefined:."""
    props = {}
    for f in fields:
        if f["kind"] == "title" or f["raw"] in (None, "", []):
            continue
        key, v = f["name"], f["raw"]
        if f["kind"] == "date":
            props["date:%s:start" % key] = v
            props["date:%s:is_datetime" % key] = 0
            continue
        if f["kind"] == "number":
            v = float(v) if "." in str(v) else int(v)
        if key.lower() in ("id", "url"):
            key = "userDefined:" + key
        props[key] = v
    return props, body.strip()


def parse_value(kind, text):
    """(raw value, error) for what was typed into a field that is not picked from a list."""
    text = text.strip()
    if not text:
        return None, ""
    if kind == "number":
        try:
            float(text)
            return text, ""
        except ValueError:
            return None, "%r is not a number" % text
    if kind == "date":
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text, ""
        return None, "a date is written 2026-11-08"
    return text, ""
