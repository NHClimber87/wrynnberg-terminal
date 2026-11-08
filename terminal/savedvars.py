"""Read WoW SavedVariables files (Lua table literals) into Python values.

SavedVariables are plain Lua assignments such as `MidasDB = { ... }`.
Tables with only consecutive integer keys from 1 become lists. Other tables
become dicts; integer keys stay ints. `nil` becomes None.
"""
import re
import sys

_TOKEN = re.compile(r"""
    (?P<ws>\s+|--[^\n]*)
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<num>-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?))
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<sym>[{}\[\]=,;])
""", re.VERBOSE | re.DOTALL)

_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "\n": "\n"}


def _unescape(body):
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        n = body[i + 1]
        if n.isdigit():
            m = re.match(r"\d{1,3}", body[i + 1:])
            out.append(chr(int(m.group(0))))
            i += 1 + len(m.group(0))
        else:
            out.append(_ESC.get(n, n))
            i += 2
    # Lua strings are bytes; WoW writes UTF-8. Re-decode the byte values.
    raw = "".join(out)
    try:
        return raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw


def _tokens(text):
    pos, n = 0, len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"unexpected character at offset {pos}: {text[pos:pos + 40]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        yield kind, m.group(kind)


class _Parser:
    def __init__(self, text):
        self.toks = list(_tokens(text))
        self.i = 0

    def peek(self, k=0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else (None, None)

    def take(self, value=None):
        tok = self.toks[self.i]
        if value is not None and tok[1] != value:
            raise ValueError(f"expected {value!r}, got {tok[1]!r} at token {self.i}")
        self.i += 1
        return tok

    def value(self):
        kind, val = self.peek()
        if kind == "str":
            self.i += 1
            return _unescape(val[1:-1])
        if kind == "num":
            self.i += 1
            if val.lower().startswith(("0x", "-0x")):
                return int(val, 16)
            return float(val) if any(c in val for c in ".eE") and not val.lower().startswith("0x") else int(val)
        if kind == "name":
            self.i += 1
            return {"true": True, "false": False, "nil": None}.get(val, val)
        if val == "{":
            return self.table()
        raise ValueError(f"unexpected token {val!r} at token {self.i}")

    def table(self):
        self.take("{")
        d, arr = {}, []
        while self.peek()[1] != "}":
            if self.peek()[1] == "[":
                self.take("[")
                key = self.value()
                self.take("]")
                self.take("=")
                d[key] = self.value()
            elif self.peek()[0] == "name" and self.peek(1)[1] == "=":
                key = self.take()[1]
                self.take("=")
                d[key] = self.value()
            else:
                arr.append(self.value())
            if self.peek()[1] in (",", ";"):
                self.i += 1
        self.take("}")
        if arr:
            for k, v in enumerate(arr, 1):
                d.setdefault(k, v)
        if d and all(isinstance(k, int) for k in d) and sorted(d) == list(range(1, len(d) + 1)):
            return [d[k] for k in range(1, len(d) + 1)]
        return d

    def assignments(self):
        out = {}
        while self.i < len(self.toks):
            name = self.take()[1]
            self.take("=")
            out[name] = self.value()
        return out


def load(path):
    """Return {global_name: value} for every assignment in the file."""
    with open(path, encoding="latin-1") as f:
        return _Parser(f.read()).assignments()


if __name__ == "__main__":
    data = load(sys.argv[1])
    for k, v in data.items():
        print(k, type(v).__name__, len(v) if hasattr(v, "__len__") else v)
