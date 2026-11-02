"""`herdr-notion digest`: what changed in a tab since the last time the digest ran."""
import json
import os
import sys
import time

from . import config as cfgmod
from . import notion as nt


def snapshot_path():
    from .tui import state_file
    return state_file("digest.json")


def load_snapshot(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data.get("pages"), dict) else None
    except (OSError, ValueError):
        return None


def save_snapshot(path, pages, status_prop, now=None):
    snap = {"at": now or time.time(),
            "pages": {p["id"]: {"status": nt.prop_text(p["properties"].get(status_prop)), "edited": p.get("last_edited_time") or "",
                                "title": nt.page_title(p)} for p in pages}}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f)


def compare(old, pages, status_prop):
    """[(kind, page, detail)] for what differs from the snapshot `old`: a status change, a new page, or an edit."""
    out = []
    for p in pages:
        before = old["pages"].get(p["id"])
        status = nt.prop_text(p["properties"].get(status_prop))
        if before is None:
            out.append(("new", p, "new in this tab" + (", " + status if status else "")))
        elif before["status"] != status:
            out.append(("status", p, "%s → %s" % (before["status"] or "no status", status or "no status")))
        elif (p.get("last_edited_time") or "") > before["edited"]:
            out.append(("edited", p, "edited %s ago" % nt.relative_time(p.get("last_edited_time"))))
    rank = {"status": 0, "new": 1, "edited": 2}
    return sorted(out, key=lambda c: rank[c[0]])


GLYPH = {"status": "●", "new": "+", "edited": "◐"}


def render(changes, tab, since, color=False):
    def paint(code, text):
        return "\x1b[%sm%s\x1b[0m" % (code, text) if color else text
    when = time.strftime("%a %H:%M", time.localtime(since)) if since else "the last digest"
    if not changes:
        return ["Nothing changed in %s since %s." % (tab, when)]
    lines = [paint("1", "Since %s · %d change%s in %s" % (when, len(changes), "" if len(changes) == 1 else "s", tab))]
    for kind, p, detail in changes:
        lines.append("  %s %s  %s" % (paint({"status": "32", "new": "36", "edited": "33"}[kind], GLYPH[kind]),
                                      nt.clip(nt.page_title(p), 40).ljust(40), paint("2", detail)))
    return lines


def bar_line(pages, cfg, groups, old=None, tab="Notion"):
    """One short plain line for herdr's tab bar: 'Notion · 4 mine · 2 review · 1 new'. The first number is the pages in the
    tab, then the two statuses in progress with the most pages, then the changes since the last digest (never saved here)."""
    prop = cfg["status_property"]
    counts = {}
    for p in pages:
        name = nt.prop_text(p["properties"].get(prop))
        if name and (nt.group_kind(groups.get(name, "")) or nt.guess_kind(name)) == "progress":
            counts[name] = counts.get(name, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:2]
    parts = ["Notion", "%d %s" % (len(pages), tab.lower())] + ["%d %s" % (n, name.lower()) for name, n in top]
    if old:
        changes = len(compare(old, pages, prop))
        if changes:
            parts.append("%d new" % changes)
    return " · ".join(parts)


def pick_tab(cfg, name):
    filters = cfg["filters"]
    if not name:
        return filters[0]
    found = next((f for f in filters if f["name"].lower() == name.lower()), None)
    if not found:
        raise cfgmod.ConfigError("no tab named %r (tabs: %s)" % (name, ", ".join(f["name"] for f in filters)))
    return found


def fetch(client, cfg, flt):
    schema = client.discover()
    if flt.get("view"):
        body = {"view_url": flt["view"], "page_size": cfg["page_size"]}
    else:
        body = nt.build_query(flt, None, nt.title_property_name(schema.get("properties", {})), "me", cfg["page_size"])
    return client.query(body)["results"]


def run(client, cfg, tab="", peek=False, path=None, color=False, now=None):
    """Lines to print. Writes the snapshot unless `peek`."""
    path = path or snapshot_path()
    flt = pick_tab(cfg, tab)
    pages = fetch(client, cfg, flt)
    prop = cfg["status_property"]
    old = load_snapshot(path)
    if old is None:
        lines = ["First digest: remembering %d pages in %s. Run it again later to see what changed." % (len(pages), flt["name"])]
    else:
        lines = render(compare(old, pages, prop), flt["name"], old.get("at"), color)
    if not peek:
        save_snapshot(path, pages, prop, now)
    return lines


def main(argv):
    args = list(argv)
    peek = "--peek" in args
    bar = "--bar" in args
    tab = ""
    if "--tab" in args:
        i = args.index("--tab")
        tab = args[i + 1] if i + 1 < len(args) else ""
    if "-h" in args or "--help" in args:
        print("usage: herdr-notion digest [--tab NAME] [--peek] [--bar]\n"
              "Prints what changed in the tab (default: the first) since the last digest. --peek leaves the last-seen state alone.\n"
              "--bar prints one short line for herdr's tab_bar_right (it never saves the last-seen state).")
        return 0
    from . import mcpbackend, oauth
    try:
        cfg = cfgmod.load()
        if not oauth.load():
            print("Not signed in. Run herdr-notion and sign in first.", file=sys.stderr)
            return 1
        client = mcpbackend.McpClient(cfg["data_source_id"])
        client.want = set(cfg["columns"])
        if bar:  # one plain line for herdr's tab bar; reads, never saves the last-seen state
            flt = pick_tab(cfg, tab)
            groups = nt.status_groups(client.discover(), cfg["status_property"])
            print(bar_line(fetch(client, cfg, flt), cfg, groups, load_snapshot(snapshot_path()), flt["name"]))
            return 0
        color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
        print("\n".join(run(client, cfg, tab, peek, color=color)))
    except (cfgmod.ConfigError, cfgmod.NeedsSetup, nt.NotionError, oauth.OAuthError) as e:
        print("digest: %s" % e, file=sys.stderr)
        return 1
    return 0
