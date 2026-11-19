"""Layer 1: static validation with sqlglot against the catalog allow-list."""
import pytest

from cohort.sqlguard import GuardError, validate

ATTACKS = {
    "multi_statement": ("SELECT COUNT(*) FROM patients; DROP TABLE patients", "single_statement"),
    "drop": ("DROP TABLE patients", "read_only"),
    "delete": ("DELETE FROM patients WHERE age_at_diagnosis < 30", "read_only"),
    "update": ("UPDATE clinical SET vital_status = 'Dead'", "read_only"),
    "insert": ("INSERT INTO patients(patient_id) VALUES ('x')", "read_only"),
    "create": ("CREATE TABLE x AS SELECT * FROM patients", "read_only"),
    "attach": ("ATTACH DATABASE '/tmp/x.db' AS x", "read_only"),
    "pragma": ("PRAGMA table_info(patients)", "read_only"),
    "sqlite_master": ("SELECT COUNT(*) FROM sqlite_master", "table_allowlist"),
    "pragma_tvf": ("SELECT COUNT(*) FROM pragma_table_info('patients')", "table_allowlist"),
    "schema_qualified": ("SELECT COUNT(*) FROM main.patients", "table_allowlist"),
    "unknown_table": ("SELECT COUNT(*) FROM persons", "table_allowlist"),
    "unknown_column": ("SELECT COUNT(*) FROM patients WHERE ssn = '1'", "column_allowlist"),
    "load_extension": ("SELECT load_extension('evil.so')", "function_allowlist"),
    "randomblob": ("SELECT COUNT(randomblob(1000000000)) FROM patients", "function_allowlist"),
    "group_concat_ids": ("SELECT group_concat(patient_id) FROM patients", "read_only"),
    "recursive_cte": ("WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM r) SELECT COUNT(*) FROM r",
                      "read_only"),
    "restricted_text_where": ("SELECT COUNT(*) FROM pathology_reports WHERE report_text LIKE '%lobular%'",
                              "access_tier"),
    "restricted_text_star": ("SELECT * FROM pathology_reports", "access_tier"),
    "restricted_in_cte": ("WITH t AS (SELECT report_text AS x FROM pathology_reports) SELECT COUNT(x) FROM t",
                          "access_tier"),
    "row_level": ("SELECT age_at_diagnosis FROM patients", "aggregate_only"),
    "row_level_over_agg_cte": ("WITH t AS (SELECT race, COUNT(*) FROM patients GROUP BY race) SELECT * FROM t",
                               "aggregate_only"),
    "identifier_group": ("SELECT patient_id, COUNT(*) FROM slide_images GROUP BY patient_id", "identifier_output"),
    "empty": ("   ", "parse"),
    "garbage": ("SELEC COUNT(*) FROM", "parse"),
}


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_attack_rejected(name):
    sql, stage = ATTACKS[name]
    with pytest.raises(GuardError) as e:
        validate(sql, "researcher")
    assert e.value.stage == stage, f"{name}: rejected at {e.value.stage}, expected {stage}"


ALLOWED = [
    "SELECT COUNT(*) FROM patients",
    "SELECT stage_group, COUNT(*) FROM clinical GROUP BY stage_group",
    "SELECT COUNT(DISTINCT patient_id) FROM slide_images WHERE slide_type = 'Diagnostic Slide'",
    "WITH t AS (SELECT patient_id FROM data_availability WHERE has_rna_tumor = 1) "
    "SELECT COUNT(*) FROM t JOIN clinical c ON c.patient_id = t.patient_id WHERE c.er_status = 'Positive'",
    "SELECT COUNT(*) FROM patients WHERE patient_id NOT IN (SELECT patient_id FROM rna_samples)",
    "SELECT COUNT(*) FROM pathology_reports WHERE n_chars > 5000",  # public columns of a restricted table
    "SELECT ROUND(AVG(age_at_diagnosis), 1), MIN(age_at_diagnosis), MAX(age_at_diagnosis) FROM patients",
    "SELECT COUNT(*) FROM patients WHERE gender = 'MALE' UNION ALL SELECT COUNT(*) FROM patients",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_legitimate_queries_pass(sql):
    v = validate(sql, "researcher")
    assert v.checks


def test_approved_role_may_read_restricted_text_and_rows():
    v = validate("SELECT patient_id, report_text FROM pathology_reports", "approved")
    assert ("pathology_reports", "report_text") in v.columns


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        validate("SELECT COUNT(*) FROM patients", "admin")
