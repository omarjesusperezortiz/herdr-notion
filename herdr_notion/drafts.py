"""Ticket drafts: what an agent hands over for you to review. `python3 -m herdr_notion draft ...` writes a small JSON file in
the plugin's state directory (no network, no sign-in, so it works from any pane); the app notices it and opens the new-ticket
box filled in. Nothing reaches Notion until you press ⏎ there."""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

from . import config as cfgmod


def drafts_dir():
    base = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.dirname(cfgmod.config_path())
    return os.path.join(base, "drafts")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "draft"


def write_draft(directory, title, fields=None, body="", note="", sender=""):
    """Save a draft; returns its path. The file is written whole and then renamed, so the app never reads half of one."""
    title = (title or "").strip()
    if not title:
        raise ValueError("a draft needs a title")
    now = datetime.now(timezone.utc)
    data = {"from": sender.strip(), "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "title": title,
            "set": dict(fields or {}), "body": body or "", "note": (note or "").strip()}
    os.makedirs(directory, exist_ok=True)
    name = "%s-%s.json" % (now.strftime("%Y%m%dT%H%M%S%f"), slug(title))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".part-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    path = os.path.join(directory, name)
    os.replace(tmp, path)
    return path


def list_drafts(directory):
    """[(path, data)] oldest first; files that are not valid drafts are skipped."""
    out = []
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        return out
    for n in names:
        path = os.path.join(directory, n)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and str(data.get("title", "")).strip():
            data.setdefault("set", {})
            data.setdefault("body", "")
            out.append((path, data))
    return out


def remove_draft(path):
    try:
        os.remove(path)
    except OSError:
        pass


def main(argv):
    ap = argparse.ArgumentParser(prog="herdr-notion draft", description="Hand a ticket draft to the herdr-notion pane for review.")
    ap.add_argument("--title", required=True)
    ap.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE", help="a field of the database, repeatable")
    ap.add_argument("--body", default="", help="the description (Notion markdown)")
    ap.add_argument("--body-file", help="read the description from a file, or - for stdin")
    ap.add_argument("--note", default="", help="a line for the reviewer: why, or a possible duplicate")
    ap.add_argument("--from", dest="sender", default="", help="who drafted it")
    ap.add_argument("--to", default="", help="the drafts directory (default: the plugin's state directory)")
    a = ap.parse_args(argv)
    fields = {}
    for item in a.set:
        if "=" not in item:
            ap.error("--set wants FIELD=VALUE, got %r" % item)
        k, v = item.split("=", 1)
        fields[k.strip()] = v.strip()
    body = a.body
    if a.body_file:
        body = sys.stdin.read() if a.body_file == "-" else open(os.path.expanduser(a.body_file), encoding="utf-8").read()
    try:
        path = write_draft(a.to or drafts_dir(), a.title, fields, body, a.note, a.sender)
    except (ValueError, OSError) as e:
        print("draft not saved: %s" % e, file=sys.stderr)
        return 1
    print("draft saved: %s\nIt is waiting for review in the herdr-notion pane (press N there). Nothing was written to Notion." % path)
    return 0
