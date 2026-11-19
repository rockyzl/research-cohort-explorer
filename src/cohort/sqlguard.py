"""Two independent layers that every query (LLM-generated or not) must pass.

Layer 1 - static validation with sqlglot (before execution):
  * parses as exactly one SQLite statement; root is SELECT / set operation;
  * no DDL/DML/PRAGMA/ATTACH/transaction nodes anywhere;
  * every base table is in the catalog; no schema-qualified or table-valued-function sources;
  * every column resolves against the catalog schema (sqlglot qualify) and is readable by the role
    (restricted columns are rejected for role "researcher"; SELECT * is expanded first);
  * functions come from an allow-list; GROUP_CONCAT is denied (it can smuggle row-level IDs into
    an "aggregate"); recursive CTEs are denied.

Layer 2 - engine enforcement in SQLite (during execution):
  * read-only URI connection (mode=ro) + PRAGMA query_only;
  * sqlite3 authorizer that independently re-checks every table/column READ against the same
    role allow-list and denies every action other than SELECT/READ/allow-listed FUNCTION;
  * progress handler aborts queries that run too long; fetchmany caps rows.

Layer 2 does not trust layer 1: tests disable layer 1 and show layer 2 still blocks each attack.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from cohort.catalog import DB_PATH, Catalog, load_catalog

MAX_ROWS = 200
TIMEOUT_S = 3.0
DIALECT = "sqlite"

ALLOWED_FUNCTIONS = {
    "count", "sum", "avg", "min", "max", "total", "round", "abs", "coalesce", "ifnull", "nullif",
    "lower", "upper", "length", "substr", "substring", "trim", "ltrim", "rtrim", "instr", "replace",
    "iif", "like", "glob", "cast", "printf", "char", "unicode", "sign", "str_position",
}
DENIED_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Pragma,
    exp.Transaction, exp.Commit, exp.Rollback, exp.Set, exp.Use, exp.Copy, exp.Merge, exp.LoadData,
    exp.GroupConcat, exp.Into,
)


class GuardError(ValueError):
    """Raised when a query is rejected. `stage` names the check that failed."""

    def __init__(self, stage: str, message: str):
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
        self.message = message


@dataclass
class Validated:
    sql: str                     # original SQL (what will be executed)
    tree: exp.Expression         # qualified tree, used by the output policy
    tables: set[str]
    columns: set[tuple[str, str]]
    checks: list[str] = field(default_factory=list)


def _fn_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower()


def validate(sql: str, role: str = "researcher", catalog: Catalog | None = None) -> Validated:
    cat = catalog or load_catalog()
    readable = cat.readable(role)
    checks = []
    if not sql or not sql.strip():
        raise GuardError("parse", "empty SQL")
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except ParseError as e:
        raise GuardError("parse", f"sqlglot could not parse the SQL: {str(e).splitlines()[0]}") from None
    if len(statements) != 1:
        raise GuardError("single_statement", f"expected exactly 1 statement, got {len(statements)}")
    tree = statements[0]
    checks.append("parsed as one SQLite statement")

    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        raise GuardError("read_only", f"only SELECT queries are allowed, got {type(tree).__name__}")
    for node in tree.walk():
        if isinstance(node, DENIED_NODES):
            raise GuardError("read_only", f"disallowed construct: {type(node).__name__}")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise GuardError("read_only", "recursive CTEs are not allowed")
    checks.append("SELECT-only, no DDL/DML/PRAGMA/ATTACH")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    tables = set()
    for t in tree.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            raise GuardError("table_allowlist", f"table-valued functions are not allowed: {t.sql(DIALECT)}")
        if t.args.get("db") or t.args.get("catalog"):
            raise GuardError("table_allowlist", f"schema-qualified tables are not allowed: {t.sql(DIALECT)}")
        name = t.name.lower()
        if name in cte_names:
            continue
        if name not in cat.tables:
            raise GuardError("table_allowlist", f"table '{t.name}' is not in the catalog")
        tables.add(name)
    checks.append(f"tables in catalog allow-list: {sorted(tables)}")

    for f in tree.find_all(exp.Func):
        if isinstance(f, (exp.Cast, exp.TryCast, exp.Case, exp.If, exp.Binary, exp.Connector, exp.Unary,
                          exp.Predicate)):
            continue  # operators/predicates that sqlglot models as Func subclasses
        name = _fn_name(f)
        if name not in ALLOWED_FUNCTIONS:
            raise GuardError("function_allowlist", f"function '{name}' is not allowed")
    checks.append("functions in allow-list")

    try:
        qualified = qualify(tree.copy(), schema=cat.sqlglot_schema(), dialect=DIALECT,
                            validate_qualify_columns=True, quote_identifiers=False)
    except OptimizeError as e:
        raise GuardError("column_allowlist", f"unresolvable column or table: {e}") from None

    columns = set()
    for scope in traverse_scope(qualified):
        for col in scope.columns:
            src = scope.sources.get(col.table)
            if isinstance(src, exp.Table):
                t, c = src.name.lower(), col.name.lower()
                if c not in cat.columns.get(t, {}):
                    raise GuardError("column_allowlist", f"column {t}.{c} is not in the catalog")
                if c not in readable[t]:
                    raise GuardError("access_tier",
                                     f"column {t}.{c} is restricted; role '{role}' is not authorized")
                columns.add((t, c))
    checks.append(f"{len(columns)} column(s) resolved against catalog and readable by role '{role}'")
    return Validated(sql=sql.strip().rstrip(";"), tree=qualified, tables=tables, columns=columns, checks=checks)


# --------------------------------------------------------------------------------------------
# Engine layer
# --------------------------------------------------------------------------------------------
def _authorizer(readable: dict[str, set[str]]):
    def auth(action, arg1, arg2, dbname, source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table, column = (arg1 or "").lower(), (arg2 or "").lower()
            if table in readable and (column == "" or column in readable[table]):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if (arg2 or "").lower() in ALLOWED_FUNCTIONS else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY
    return auth


def connect_readonly(role: str = "researcher", db_path: Path = DB_PATH, catalog: Catalog | None = None):
    cat = catalog or load_catalog()
    if not Path(db_path).exists():
        raise FileNotFoundError(f"{db_path} missing; run `make data`")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    con.execute("PRAGMA query_only = ON")
    con.set_authorizer(_authorizer(cat.readable(role)))
    return con


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_ms: float


def execute(con: sqlite3.Connection, sql: str, max_rows: int = MAX_ROWS, timeout_s: float = TIMEOUT_S) -> Result:
    deadline = time.monotonic() + timeout_s
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    started = time.monotonic()
    try:
        cur = con.execute(sql)
        rows = cur.fetchmany(max_rows + 1)
    except sqlite3.DatabaseError as e:
        msg = str(e)
        if "interrupted" in msg:
            raise GuardError("timeout", f"query exceeded {timeout_s}s") from None
        if "not authorized" in msg or "prohibited" in msg or "access to" in msg:
            raise GuardError("engine_authorizer", msg) from None
        raise GuardError("execution", msg) from None
    finally:
        con.set_progress_handler(None, 0)
    cols = [d[0] for d in cur.description] if cur.description else []
    return Result(columns=cols, rows=rows[:max_rows], truncated=len(rows) > max_rows,
                  elapsed_ms=round((time.monotonic() - started) * 1000, 1))
