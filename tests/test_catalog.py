"""Catalog <-> database consistency and allow-list derivation."""
import sqlite3

import pytest

from cohort.catalog import DB_PATH, load_catalog


def test_restricted_columns_hidden_from_researcher():
    cat = load_catalog()
    assert "report_text" not in cat.readable("researcher")["pathology_reports"]
    assert "report_text" in cat.readable("approved")["pathology_reports"]
    assert "n_chars" in cat.readable("researcher")["pathology_reports"]


def test_free_text_flagged():
    assert load_catalog().free_text_columns() == {("pathology_reports", "report_text")}


def test_prompt_schema_omits_restricted_columns():
    prompt = load_catalog().as_prompt("researcher")
    assert "report_text" not in prompt and "n_chars" in prompt


def test_every_column_documented():
    cat = load_catalog()
    for t, cols in cat.columns.items():
        assert cat.tables[t]["description"]
        for c, col in cols.items():
            assert col.description and col.type in ("TEXT", "INTEGER", "REAL"), (t, c)
            assert col.access_tier in ("public", "restricted")


def test_search_finds_receptor_columns():
    hits = load_catalog().search("estrogen receptor")
    assert any(h["column"] == "er_status" for h in hits)


@pytest.mark.needs_db
def test_database_matches_catalog_exactly():
    cat = load_catalog()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    db_tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert db_tables == set(cat.tables)
    for t, cols in cat.columns.items():
        db_cols = [(r[1], r[2]) for r in con.execute(f'PRAGMA table_info("{t}")')]
        assert db_cols == [(c, col.type) for c, col in cols.items()], t


@pytest.mark.needs_db
def test_loaded_values_within_allowed_values():
    cat = load_catalog()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    for t, cols in cat.columns.items():
        for c, col in cols.items():
            if col.allowed_values:
                vals = {r[0] for r in con.execute(f'SELECT DISTINCT "{c}" FROM "{t}" WHERE "{c}" IS NOT NULL')}
                assert vals <= set(col.allowed_values), (t, c, vals - set(col.allowed_values))
