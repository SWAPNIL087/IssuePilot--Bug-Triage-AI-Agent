"""Runbooks: the only place an action's SQL is allowed to live. See mcp/runbooks/README.md."""
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from triageguard.frontmatter import split_front_matter

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STATEMENTS = ("SELECT", "INSERT", "UPDATE")  # no DELETE, DDL, MERGE or EXEC, ever


class RunbookError(Exception):
    """A runbook file is malformed."""


class ActionError(Exception):
    """An action could not be validated or executed."""


@dataclass(frozen=True)
class Param:
    name: str
    source: str  # ticket | channel | system | db:<column>
    rule: str    # regex:<re> | enum:a,b | email | any

    def check(self, value) -> bool:
        if value is None or isinstance(value, (dict, list)) or not str(value).strip():
            return False
        text = str(value)
        kind, _, arg = self.rule.partition(":")
        if kind == "regex":
            return re.fullmatch(arg, text) is not None
        if kind == "enum":
            return text in arg.split(",")
        if kind == "email":
            return EMAIL.match(text) is not None
        return True

    @property
    def hint(self) -> str:
        kind, _, arg = self.rule.partition(":")
        if kind == "enum":
            return "one of " + ", ".join(arg.split(","))
        if kind == "regex":
            return f"format {arg}"
        return "text"


@dataclass(frozen=True)
class Query:
    name: str
    sql: str
    expect_rows: int | None

    @property
    def returns_rows(self) -> bool:
        return self.sql.lstrip().upper().startswith("SELECT")


@dataclass(frozen=True)
class Runbook:
    id: str
    version: int
    risk: str            # read | write
    mode: str            # shadow | approval | auto
    precheck: str
    purpose: str
    params: tuple[Param, ...]
    transitions: tuple[tuple[str, str, str], ...]
    queries: tuple[Query, ...]
    reply: str
    pending: str
    missing: str
    hash: str

    def params_from(self, *sources: str) -> list[Param]:
        return [p for p in self.params if p.source in sources]

    def transition_mode(self, old: str, new: str) -> str | None:
        return next((mode for frm, to, mode in self.transitions if (frm, to) == (old, new)), None)


def _sections(body: str) -> dict[str, str]:
    parts = re.split(r"^## +(.+?) *$", body, flags=re.M)
    return {parts[i].strip().lower(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _table(text: str) -> list[list[str]]:
    rows = [[c.strip().strip("`") for c in line.strip().strip("|").split("|")]
            for line in text.splitlines() if line.strip().startswith("|")]
    return [r for r in rows[2:]]  # drop header and separator


def _fenced(text: str, kind: str) -> str:
    m = re.search(rf"```{kind}\n(.*?)```", text, re.S)
    return m.group(1).strip() if m else ""


def parse_runbook(path: Path) -> Runbook:
    raw = Path(path).read_bytes().decode("utf-8").replace("\r\n", "\n")
    meta, body = split_front_matter(raw)
    where = Path(path).name
    for key in ("id", "version", "risk", "mode"):
        if key not in meta:
            raise RunbookError(f"{where}: missing front matter '{key}'")
    if meta["risk"] not in ("read", "write") or meta["mode"] not in ("shadow", "approval", "auto"):
        raise RunbookError(f"{where}: invalid risk or mode")

    sections = _sections(body)
    for needed in ("purpose", "parameters", "queries", "reply template"):
        if needed not in sections:
            raise RunbookError(f"{where}: missing section '{needed}'")

    params = tuple(Param(*row[:3]) for row in _table(sections["parameters"]))
    names = {p.name for p in params}
    queries = []
    for attrs, sql in re.findall(r"```sql([^\n]*)\n(.*?)```", sections["queries"], re.S):
        opts = dict(re.findall(r"(\w+)=(\S+)", attrs))
        sql = sql.strip()
        if not sql.upper().startswith(STATEMENTS):
            raise RunbookError(f"{where}: only {', '.join(STATEMENTS)} statements are allowed")
        if ";" in sql.rstrip(";"):
            raise RunbookError(f"{where}: one statement per query block")
        if sql.upper().startswith("UPDATE") and ":" not in sql.upper().partition("WHERE")[2]:
            raise RunbookError(f"{where}: UPDATE needs a WHERE clause with bound parameters")
        unknown = set(re.findall(r":(\w+)", sql)) - names
        if unknown:
            raise RunbookError(f"{where}: query uses undeclared parameters {sorted(unknown)}")
        queries.append(Query(opts.get("name", f"q{len(queries) + 1}"), sql.rstrip(";"),
                             int(opts["expect_rows"]) if "expect_rows" in opts else None))
    if not queries:
        raise RunbookError(f"{where}: no queries")

    transitions = tuple((r[0], r[1], r[2]) for r in _table(sections.get("transitions", "")))
    if meta["risk"] == "write":
        if not transitions or not any(q.name == "audit" for q in queries):
            raise RunbookError(f"{where}: a write runbook needs a transitions table and an 'audit' query")
        if not any(p.name == "request_id" for p in params):
            raise RunbookError(f"{where}: a write runbook needs a request_id parameter")
        if any(q.expect_rows != 1 for q in queries):
            raise RunbookError(f"{where}: every write query must set expect_rows=1")

    return Runbook(
        id=meta["id"], version=int(meta["version"]), risk=meta["risk"], mode=meta["mode"],
        precheck=meta.get("precheck", ""),
        purpose=sections["purpose"].strip(), params=params, transitions=transitions, queries=tuple(queries),
        reply=_fenced(sections["reply template"], "text"),
        pending=_fenced(sections.get("pending message", ""), "text"),
        missing=_fenced(sections.get("missing information", ""), "text"),
        hash=hashlib.sha256(raw.encode()).hexdigest()[:16],
    )


def load_runbooks(directory: Path) -> dict[str, Runbook]:
    books = {}
    for path in sorted(Path(directory).glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        book = parse_runbook(path)
        if book.id in books:
            raise RunbookError(f"duplicate runbook id {book.id}")
        books[book.id] = book
    for book in books.values():
        if book.precheck and book.precheck not in books:
            raise RunbookError(f"{book.id}: unknown precheck {book.precheck}")
    return books
