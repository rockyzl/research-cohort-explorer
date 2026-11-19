"""Output policy applied to every result shown to a user or exported.

Rules (see README "Guardrails"):
  1. Small-cell suppression, threshold n < 11 (the CMS cell-size convention), for aggregate results:
     a. If the outermost SELECT aggregates, the query is rewritten to add a hidden cell size
        (COUNT(DISTINCT patient_id) when the FROM has a patient-keyed table, else COUNT(*)).
        Rows whose cell size is 1-10 have every statistic (count, avg, min, max...) blanked,
        not only the counts, because an average over 3 patients is also disclosive.
     b. Any output column produced by COUNT/SUM (in any sub-query) has values 1-10 blanked.
     c. Secondary suppression: if exactly one cell in a count column was blanked, the next
        smallest non-zero cell is blanked too, so the hidden value cannot be recovered by
        subtraction within that column. Cross-query differencing is NOT prevented (limitation).
  2. Free-text columns never appear in exports; row-level results cannot be exported.
  3. Rows are capped (sqlguard.MAX_ROWS).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from cohort.catalog import Catalog, load_catalog
from cohort.sqlguard import is_aggregate_select, outer_selects

THRESHOLD = 11
CELL_COL = "__cell_n"
SUPPRESSED = "<11"  # marker string; genuine NULLs stay None


@dataclass
class Policy:
    aggregate: bool
    rewritten_sql: str | None
    count_columns: set[str]
    stat_columns: set[str]
    notes: list[str] = field(default_factory=list)


def _contains_agg(e: exp.Expression) -> bool:
    for node in e.walk():
        if isinstance(node, (exp.Subquery, exp.Select)) and node is not e:
            continue
        if isinstance(node, exp.AggFunc):
            return True
    return False


def _contains_count(e: exp.Expression) -> bool:
    return any(isinstance(n, (exp.Count, exp.Sum)) for n in e.walk())


def plan(tree: exp.Expression, catalog: Catalog | None = None) -> Policy:
    """`tree` must be the QUALIFIED tree from sqlguard.validate, and the SQL that is executed must be
    generated from it (rewritten_sql), so output column names match the aliases analysed here.
    (Executing the user's original text instead let an unaliased COUNT(*) inside a CTE escape
    suppression - see docs/build-log.md.)"""
    cat = catalog or load_catalog()
    count_cols: set[str] = set()
    for scope in traverse_scope(tree):
        if isinstance(scope.expression, exp.Select):
            for proj in scope.expression.expressions:
                if _contains_count(proj):
                    count_cols.add(proj.alias_or_name.lower())

    rewritten = tree.copy()
    leaves = outer_selects(rewritten)
    agg_leaves = [s for s in leaves if is_aggregate_select(s)]
    stat_cols: set[str] = set()
    notes = []
    # The hidden cell-size column must be added to every leaf of a set operation or to none.
    if agg_leaves and len(agg_leaves) == len(leaves):
        root_scopes = {id(sc.expression): sc for sc in traverse_scope(rewritten)}
        for sel in leaves:
            stat_cols |= {p.alias_or_name.lower() for p in sel.expressions if _contains_agg(p)}
            sc = root_scopes.get(id(sel))
            cell = None
            for alias, src in (sc.sources.items() if sc else []):
                if isinstance(src, exp.Table) and "patient_id" in cat.columns.get(src.name.lower(), {}):
                    cell = exp.Count(this=exp.Distinct(expressions=[exp.column("patient_id", table=alias)]))
                    notes.append(f"cell size = distinct patients in {src.name}")
                    break
            if cell is None:
                cell = exp.Count(this=exp.Star())
                notes.append("cell size = contributing rows (no patient-keyed base table in outer FROM)")
            sel.select(exp.alias_(cell, CELL_COL), copy=False)
        aggregate = True
    else:
        aggregate = False
        notes.append("row-level result: only count-column suppression applies")
    return Policy(aggregate=aggregate, rewritten_sql=rewritten.sql("sqlite"), count_columns=count_cols,
                  stat_columns=stat_cols, notes=notes)


def apply(columns: list[str], rows: list[tuple], policy: Policy) -> tuple[list[str], list[list], int]:
    """Return (columns, rows, n_suppressed) with the hidden cell column removed."""
    rows = [list(r) for r in rows]
    lower = [c.lower() for c in columns]
    suppressed = 0
    cell_idx = lower.index(CELL_COL) if CELL_COL in lower else None
    if cell_idx is not None:
        stat_idx = [i for i, c in enumerate(lower) if c in policy.stat_columns]
        for r in rows:
            n = r[cell_idx]
            if n is not None and 0 < n < THRESHOLD:
                for i in stat_idx:
                    if r[i] != SUPPRESSED:
                        r[i] = SUPPRESSED
                        suppressed += 1
    count_idx = [i for i, c in enumerate(lower) if c in policy.count_columns and i != cell_idx]
    for i in count_idx:
        for r in rows:
            v = r[i]
            if isinstance(v, (int, float)) and 0 < v < THRESHOLD:
                r[i] = SUPPRESSED
                suppressed += 1
        # secondary suppression within the column
        blanked = [r for r in rows if r[i] == SUPPRESSED]
        if len(blanked) == 1 and len(rows) > 1:
            others = [r for r in rows if isinstance(r[i], (int, float)) and r[i] > 0]
            if others:
                victim = min(others, key=lambda r: r[i])
                victim[i] = SUPPRESSED
                suppressed += 1
    if cell_idx is not None:
        columns = [c for j, c in enumerate(columns) if j != cell_idx]
        rows = [[v for j, v in enumerate(r) if j != cell_idx] for r in rows]
    return columns, rows, suppressed


def exportable(columns_read: set[tuple[str, str]], policy: Policy, catalog: Catalog | None = None) -> tuple[bool, str]:
    cat = catalog or load_catalog()
    if columns_read & cat.free_text_columns():
        return False, "query reads a free-text column; free text is never exported"
    if not policy.aggregate:
        return False, "only aggregate results can be exported"
    return True, "aggregate result with small-cell suppression"
