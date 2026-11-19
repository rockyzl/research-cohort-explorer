"""Load the YAML catalog and derive the schema / allow-lists from it."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "catalog" / "catalog.yaml"
DB_PATH = ROOT / "data" / "cohort.sqlite"

ROLES = ("researcher", "approved")  # researcher = default; approved = authorized for restricted tier


@dataclass(frozen=True)
class Column:
    table: str
    name: str
    type: str
    description: str
    access_tier: str
    free_text: bool
    unit: str | None
    allowed_values: tuple | None
    source: str | None
    omop_like: str | None


class Catalog:
    def __init__(self, raw: dict):
        self.raw = raw
        self.tables: dict[str, dict] = raw["tables"]
        self.columns: dict[str, dict[str, Column]] = {}
        for t, spec in self.tables.items():
            cols = {}
            for c, cs in spec["columns"].items():
                cols[c] = Column(
                    table=t, name=c, type=cs["type"], description=cs["description"],
                    access_tier=cs.get("access_tier", spec["access_tier"]),
                    free_text=bool(cs.get("free_text", False)), unit=cs.get("unit"),
                    allowed_values=tuple(cs["allowed_values"]) if cs.get("allowed_values") else None,
                    source=cs.get("source"), omop_like=cs.get("omop_like"))
            self.columns[t] = cols

    # -- allow-lists ---------------------------------------------------------------------------
    def readable(self, role: str) -> dict[str, set[str]]:
        """Table -> set of columns this role may read anywhere in a query."""
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        out = {}
        for t, cols in self.columns.items():
            out[t] = {c for c, col in cols.items() if col.access_tier == "public" or role == "approved"}
        return out

    def free_text_columns(self) -> set[tuple[str, str]]:
        return {(t, c) for t, cols in self.columns.items() for c, col in cols.items() if col.free_text}

    def sqlglot_schema(self) -> dict[str, dict[str, str]]:
        return {t: {c: col.type for c, col in cols.items()} for t, cols in self.columns.items()}

    def ddl(self, table: str) -> str:
        spec = self.tables[table]
        cols = [f'"{c}" {col.type}' for c, col in self.columns[table].items()]
        pk = ", ".join(f'"{c}"' for c in spec["primary_key"])
        return f'CREATE TABLE "{table}" ({", ".join(cols)}, PRIMARY KEY ({pk}))'

    # -- search ------------------------------------------------------------------------------
    def search(self, query: str) -> list[dict]:
        terms = [t for t in query.lower().split() if t]
        hits = []
        for t, cols in self.columns.items():
            tdesc = self.tables[t]["description"]
            for c, col in cols.items():
                hay = " ".join(str(x) for x in (t, c, col.description, col.unit or "", col.source or "",
                                                col.omop_like or "", " ".join(map(str, col.allowed_values or ())),
                                                tdesc)).lower()
                score = sum(hay.count(term) for term in terms)
                if terms and all(term in hay for term in terms):
                    hits.append({"table": t, "column": c, "type": col.type, "description": col.description,
                                 "unit": col.unit, "access_tier": col.access_tier, "score": score})
        return sorted(hits, key=lambda h: (-h["score"], h["table"], h["column"]))

    def as_prompt(self, role: str) -> str:
        """Compact schema description for the NL-to-SQL prompt (only columns the role may read)."""
        readable = self.readable(role)
        lines = []
        for t, spec in self.tables.items():
            lines.append(f"TABLE {t} -- {spec['description']} (grain: {spec['grain']})")
            for c, col in self.columns[t].items():
                if c not in readable[t]:
                    continue
                extra = []
                if col.unit:
                    extra.append(f"unit={col.unit}")
                if col.allowed_values:
                    extra.append("values=" + "|".join(map(str, col.allowed_values)))
                lines.append(f"  {c} {col.type}: {col.description}" + (f" [{'; '.join(extra)}]" if extra else ""))
        lines.append("RELATIONSHIPS: " + "; ".join(self.raw["relationships"]))
        return "\n".join(lines)


@lru_cache(maxsize=1)
def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    return Catalog(yaml.safe_load(Path(path).read_text()))
