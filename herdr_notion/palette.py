"""Colors for the list: a status ramp that follows the workflow, Notion's color names, the nearest xterm-256 color,
and the selection background of the user's herdr theme."""
import os
import re

from . import notion as nt

KEYS = ("grey", "red", "blue", "purple", "teal", "amber", "orange", "green", "accent")

# xterm-256 index of each color, and the base-8 color it folds back to (0 black .. 7 white)
XTERM = {"grey": 245, "red": 167, "blue": 75, "purple": 140, "teal": 73, "amber": 179, "orange": 173, "green": 114, "accent": 80}
BASE8 = {"grey": 7, "red": 1, "blue": 4, "purple": 5, "teal": 6, "amber": 3, "orange": 3, "green": 2, "accent": 6}
NOTION = {"red": "red", "orange": "orange", "yellow": "amber", "green": "green", "blue": "blue", "purple": "purple",
          "pink": "purple", "brown": "orange", "gray": "grey", "grey": "grey"}
RAMP = ("blue", "purple", "teal", "amber", "orange")  # early to late in a workflow
BLOCKED_WORDS = ("block", "hold", "wait", "stuck", "pause")
DEFAULT_SELECTION = 237


def kind_of(name, groups):
    return nt.group_kind(groups.get(name, "")) or nt.guess_kind(name)


def is_blocked(name):
    return any(w in name.lower() for w in BLOCKED_WORDS)


def status_colors(options, groups, overrides=None):
    """{status name: color key}. Blocked-like names are red; to do is grey; done is green; the statuses in progress take
    colors along RAMP in the order Notion lists them. `overrides` ({name: color key}) wins."""
    progress = [o for o in options if not is_blocked(o) and kind_of(o, groups) == "progress"]
    out = {}
    for name in options:
        if is_blocked(name):
            out[name] = "red"
        elif name in progress:
            i = progress.index(name)
            out[name] = RAMP[round(i * (len(RAMP) - 1) / max(len(progress) - 1, 1))]
        else:
            kind = kind_of(name, groups)
            out[name] = "green" if kind == "done" else "grey" if kind in ("todo", "") else "blue"
    for name, key in (overrides or {}).items():
        if key in KEYS:
            out[name] = key
    return out


def status_glyphs(options, groups):
    """{status name: glyph}: blocked ⊘, to do ○, done ✓, and a fuller circle the later a status in progress comes."""
    progress = [o for o in options if not is_blocked(o) and kind_of(o, groups) == "progress"]
    out = {}
    for name in options:
        if is_blocked(name):
            out[name] = "⊘"
        elif name in progress:
            frac = progress.index(name) / max(len(progress) - 1, 1)
            out[name] = "◐" if frac < 0.34 else "◑" if frac < 0.67 else "◕"
        else:
            out[name] = "✓" if kind_of(name, groups) == "done" else "○"
    return out


def notion_color(name):
    """Color key for a Notion color name ('red', 'gray', 'default'…), or None for default/unknown."""
    return NOTION.get((name or "").lower())


def nearest_256(hex_color):
    """Index (16..255) of the xterm color closest to '#rrggbb'; None if it is not a color."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})", (hex_color or "").strip())
    if not m:
        return None
    r, g, b = (int(x, 16) for x in m.groups())
    levels = (0, 95, 135, 175, 215, 255)
    best, best_d = None, None
    for i in range(16, 256):
        if i < 232:
            n = i - 16
            c = (levels[n // 36], levels[(n // 6) % 6], levels[n % 6])
        else:
            v = 8 + (i - 232) * 10
            c = (v, v, v)
        d = (c[0] - r) ** 2 + (c[1] - g) ** 2 + (c[2] - b) ** 2
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


def herdr_selection_bg(path=None):
    """xterm-256 index of `active_row_bg` under [theme.custom] in herdr's config.toml, or None."""
    path = path or os.path.expanduser("~/.config/herdr/config.toml")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r"^\[theme\.custom\]\s*$(.*?)(?=^\[|\Z)", text, re.S | re.M)
    if not m:
        return None
    v = re.search(r"^\s*active_row_bg\s*=\s*[\"']([^\"']+)[\"']", m.group(1), re.M)
    return nearest_256(v.group(1)) if v else None


THEMES = ("default", "light", "high-contrast", "mono")
# for a light terminal background: deeper colors, a pale selection
LIGHT = {"grey": 242, "red": 124, "blue": 25, "purple": 91, "teal": 30, "amber": 130, "orange": 166, "green": 28, "accent": 31}
LIGHT_SELECTION = 254
# the bright ANSI colors (9 to 15) for a high-contrast look; the selection stays reverse video
HIGH = {"grey": 15, "red": 9, "blue": 12, "purple": 13, "teal": 14, "amber": 11, "orange": 11, "green": 10, "accent": 14}


def theme_name(name):
    return name if name in THEMES else "default"


def theme_palette(name, herdr_path=None):
    """(xterm colors by role, selection background or None for reverse video) of a theme; None for 'mono' (no colors).
    'default' follows herdr's theme for the selection when it sets one."""
    name = theme_name(name)
    if name == "mono":
        return None
    if name == "light":
        return LIGHT, LIGHT_SELECTION
    if name == "high-contrast":
        return HIGH, None
    return XTERM, herdr_selection_bg(herdr_path) or DEFAULT_SELECTION


def next_theme(name):
    return THEMES[(THEMES.index(theme_name(name)) + 1) % len(THEMES)]
