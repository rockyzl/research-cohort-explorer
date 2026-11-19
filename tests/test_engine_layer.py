"""Layer 2: the SQLite engine blocks attacks even when layer 1 (validate) is skipped entirely."""
import sqlite3

import pytest

from cohort.catalog import DB_PATH
from cohort.sqlguard import GuardError, connect_readonly, execute

pytestmark = pytest.mark.needs_db

ENGINE_ATTACKS = [
    "SELECT report_text FROM pathology_reports LIMIT 1",          # restricted column
    "SELECT * FROM pathology_reports LIMIT 1",                    # star reaches restricted column
    "SELECT COUNT(*) FROM pathology_reports WHERE report_text LIKE '%a%'",
    "SELECT name FROM sqlite_master",
    "SELECT * FROM pragma_table_info('patients')",
    "SELECT load_extension('evil.so')",
    "SELECT randomblob(10)",
    "WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM r) SELECT COUNT(*) FROM r",
    "ATTACH DATABASE ':memory:' AS x",
    "DELETE FROM patients",
    "UPDATE clinical SET vital_status = 'Dead'",
    "CREATE TABLE x (a)",
    "PRAGMA query_only = OFF",
]


@pytest.mark.parametrize("sql", ENGINE_ATTACKS)
def test_engine_blocks_without_static_layer(sql):
    con = connect_readonly("researcher")
    with pytest.raises(GuardError) as e:
        execute(con, sql)
    assert e.value.stage in ("engine_authorizer", "execution")


def test_multi_statement_rejected_by_driver():
    con = connect_readonly("researcher")
    with pytest.raises((GuardError, sqlite3.ProgrammingError)):
        execute(con, "SELECT 1; DROP TABLE patients")


def test_approved_role_engine_allows_text():
    con = connect_readonly("approved")
    res = execute(con, "SELECT report_text FROM pathology_reports LIMIT 1")
    assert isinstance(res.rows[0][0], str)


def test_read_only_even_with_permissive_authorizer():
    """mode=ro is a third, independent barrier: with the authorizer allowing everything, writes still fail."""
    con = connect_readonly("researcher")
    con.set_authorizer(lambda *a: sqlite3.SQLITE_OK)
    con.execute("PRAGMA query_only = OFF")
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("DELETE FROM patients")
    raw = sqlite3.connect(DB_PATH)
    assert raw.execute("SELECT COUNT(*) FROM patients").fetchone()[0] > 0


def test_row_cap_and_truncation_flag():
    con = connect_readonly("approved")
    res = execute(con, "SELECT patient_id FROM patients", max_rows=50)
    assert len(res.rows) == 50 and res.truncated


def test_timeout_aborts_runaway_query():
    con = connect_readonly("researcher")
    with pytest.raises(GuardError) as e:
        execute(con, "SELECT COUNT(*) FROM gene_expression a, gene_expression b", timeout_s=0.2)
    assert e.value.stage == "timeout"
