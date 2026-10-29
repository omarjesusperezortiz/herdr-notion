"""TOML loading: tomllib on Python 3.11+, a small subset parser before that.

The subset covers what config.example.toml uses: comments, [tables],
[[arrays.of.tables]], strings (basic, literal, multi-line), integers, floats,
booleans, arrays (also multi-line) and inline tables.
"""
import re

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover - exercised on older Pythons
    tomllib = None


def loads(text):
    if tomllib is not None:
        return tomllib.loads(text)
    return _Parser(text).parse()


_ESC = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}


class _Parser:
    def __init__(self, text):
        self.s = text
        self.i = 0

    def err(self, msg):
        line = self.s.count("\n", 0, self.i) + 1
        raise ValueError("TOML parse error on line %d: %s" % (line, msg))

    # -- helpers
    def skip_ws(self, newlines=False):
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t" or (newlines and c in "\r\n"):
                self.i += 1
            elif c == "#":
                while self.i < len(self.s) and self.s[self.i] != "\n":
                    self.i += 1
            else:
                break

    def parse(self):
        root = {}
        cur = root
        while True:
            self.skip_ws(newlines=True)
            if self.i >= len(self.s):
                return root
            if self.s.startswith("[[", self.i):
                self.i += 2
                path = self.key_path("]")
                if not self.s.startswith("]]", self.i):
                    self.err("expected ]]")
                self.i += 2
                parent = self.walk(root, path[:-1])
                arr = parent.setdefault(path[-1], [])
                cur = {}
                arr.append(cur)
            elif self.s[self.i] == "[":
                self.i += 1
                path = self.key_path("]")
                self.i += 1
                cur = self.walk(root, path)
            else:
                path = self.key_path("=")
                self.i += 1
                self.skip_ws()
                val = self.value()
                tgt = self.walk(cur, path[:-1])
                tgt[path[-1]] = val
            self.skip_ws()
            if self.i < len(self.s) and self.s[self.i] not in "\r\n":
                self.err("unexpected text after value")

    def walk(self, node, path):
        for k in path:
            node = node.setdefault(k, {})
            if isinstance(node, list):
                node = node[-1]
        return node

    def key_path(self, end):
        parts = []
        while True:
            self.skip_ws()
            if self.s[self.i] in "\"'":
                parts.append(self.string())
            else:
                m = re.compile(r"[A-Za-z0-9_-]+").match(self.s, self.i)
                if not m:
                    self.err("bad key")
                parts.append(m.group())
                self.i = m.end()
            self.skip_ws()
            if self.s[self.i] == ".":
                self.i += 1
                continue
            if self.s[self.i] == end:
                return parts
            self.err("bad key path")

    # -- values
    def value(self):
        c = self.s[self.i]
        if c in "\"'":
            return self.string()
        if c == "[":
            return self.array()
        if c == "{":
            return self.inline_table()
        m = re.compile(r"[^\s,\]\}#]+").match(self.s, self.i)
        if not m:
            self.err("bad value")
        tok = m.group()
        self.i = m.end()
        if tok == "true":
            return True
        if tok == "false":
            return False
        try:
            return int(tok.replace("_", ""))
        except ValueError:
            try:
                return float(tok.replace("_", ""))
            except ValueError:
                self.err("unsupported value %r" % tok)

    def string(self):
        q = self.s[self.i]
        triple = self.s.startswith(q * 3, self.i)
        if triple:
            self.i += 3
            if self.s.startswith("\r\n", self.i):
                self.i += 2
            elif self.s.startswith("\n", self.i):
                self.i += 1
        else:
            self.i += 1
        out = []
        while True:
            if self.i >= len(self.s):
                self.err("unterminated string")
            if triple and self.s.startswith(q * 3, self.i):
                self.i += 3
                while self.s.startswith(q, self.i):  # up to two extra quotes
                    out.append(q)
                    self.i += 1
                return "".join(out)
            c = self.s[self.i]
            if not triple and c == q:
                self.i += 1
                return "".join(out)
            if not triple and c == "\n":
                self.err("newline in string")
            if q == '"' and c == "\\":
                n = self.s[self.i + 1]
                if n in _ESC:
                    out.append(_ESC[n])
                    self.i += 2
                elif n in "uU":
                    ln = 4 if n == "u" else 8
                    out.append(chr(int(self.s[self.i + 2:self.i + 2 + ln], 16)))
                    self.i += 2 + ln
                elif triple and n in " \t\r\n":  # line-ending backslash
                    self.i += 1
                    while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
                        self.i += 1
                else:
                    self.err("bad escape")
                continue
            out.append(c)
            self.i += 1

    def array(self):
        self.i += 1
        items = []
        while True:
            self.skip_ws(newlines=True)
            if self.s[self.i] == "]":
                self.i += 1
                return items
            items.append(self.value())
            self.skip_ws(newlines=True)
            if self.s[self.i] == ",":
                self.i += 1

    def inline_table(self):
        self.i += 1
        out = {}
        while True:
            self.skip_ws(newlines=True)
            if self.s[self.i] == "}":
                self.i += 1
                return out
            path = self.key_path("=")
            self.i += 1
            self.skip_ws()
            self.walk(out, path[:-1])[path[-1]] = self.value()
            self.skip_ws(newlines=True)
            if self.s[self.i] == ",":
                self.i += 1
