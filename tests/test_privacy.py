"""Output policy: small-cell suppression (n<11), secondary suppression, export rules."""
import pytest
import yaml

from cohort import privacy
from cohort.catalog import ROOT
from cohort.service import build_cohort, cohort_sql, run_sql, run_sql_raw

pytestmark = pytest.mark.needs_db


def test_small_group_count_suppressed():
    r = run_sql("SELECT race, COUNT(*) FROM patients GROUP BY race")
    rows = {row[0]: row[1] for row in r["rows"]}
    assert rows["AMERICAN INDIAN OR ALASKA NATIVE"] == "<11"     # raw value 1
    assert rows["WHITE"] == 752


def test_secondary_suppression_blanks_next_smallest():
    raw = dict(run_sql_raw("SELECT race, COUNT(*) FROM patients WHERE race IS NOT NULL GROUP BY race").rows)
    r = run_sql("SELECT race, COUNT(*) FROM patients WHERE race IS NOT NULL GROUP BY race")
    shown = {row[0]: row[1] for row in r["rows"]}
    assert raw["ASIAN"] >= 11 and shown["ASIAN"] == privacy.SECONDARY   # complementary cell, not itself small
    assert r["suppressed_cells"] == 2


def test_non_count_statistics_suppressed_for_small_cells():
    # 12 male patients overall; restricting to age > 70 leaves a cell < 11.
    r = run_sql("SELECT gender, AVG(age_at_diagnosis), MAX(age_at_diagnosis) FROM patients "
                "WHERE age_at_diagnosis > 70 GROUP BY gender")
    male = next(row for row in r["rows"] if row[0] == "MALE")
    assert male[1] == "<11" and male[2] == "<11"


def test_single_scalar_count_suppressed():
    r = run_sql("SELECT COUNT(*) FROM clinical WHERE stage_group = 'IV' AND triple_negative = 1")
    assert r["rows"] == [["<11"]]


def test_union_branch_counts_suppressed():
    r = run_sql("SELECT COUNT(*) FROM patients WHERE gender = 'MALE' AND age_at_diagnosis > 70 "
                "UNION ALL SELECT COUNT(*) FROM patients")
    assert r["rows"][0] == ["<11"]


def test_zero_is_not_suppressed():
    r = run_sql("SELECT COUNT(*) FROM patients WHERE age_at_diagnosis > 200")
    assert r["rows"] == [[0]]


def test_hidden_cell_column_never_shown():
    r = run_sql("SELECT stage_group, COUNT(*) FROM clinical GROUP BY stage_group")
    assert privacy.CELL_COL not in r["columns"]
    assert privacy.CELL_COL in r["executed_sql"]


def test_executed_sql_is_shown_and_counts_reported():
    r = run_sql("SELECT COUNT(*) FROM patients")
    assert r["ok"] and r["executed_sql"] and r["row_count"] == 1 and r["checks"]


def test_free_text_never_exportable():
    r = run_sql("SELECT COUNT(*) FROM pathology_reports WHERE report_text LIKE '%lobular%'", role="approved")
    assert r["ok"] and not r["exportable"]


def test_row_level_not_exportable():
    r = run_sql("SELECT patient_id, age_at_diagnosis FROM patients", role="approved")
    assert r["ok"] and not r["exportable"] and r["truncated"]


def test_cohort_builder_suppresses_small_cohort():
    out = build_cohort({"stage_group": ["IV"], "triple_negative": True})
    assert all(v == "<11" for v in out["summary"]["rows"][0])


def test_cohort_builder_rejects_unknown_values():
    out = build_cohort({"er_status": ["Positive'); DROP TABLE patients; --"]})
    assert not out["ok"] and out["error_stage"] == "cohort_filter"


def test_cohort_builder_counts_match_raw():
    out = build_cohort({"er_status": ["Positive"], "require": ["has_rna_tumor"]})
    raw = run_sql_raw("SELECT COUNT(*) FROM clinical c JOIN data_availability a ON a.patient_id = c.patient_id "
                      "WHERE c.er_status = 'Positive' AND a.has_rna_tumor = 1").rows[0][0]
    assert out["summary"]["rows"][0][0] == raw


def test_within_row_complement_cohort_builder():
    """Basal cohort: patients=139 and with_survival=138 would reveal 1 patient without survival data."""
    out = build_cohort({"pam50_subtype": ["Basal"]})
    s = out["summary"]
    row = dict(zip(s["columns"], s["rows"][0]))
    raw = run_sql_raw(cohort_sql({"pam50_subtype": ["Basal"]})).rows[0]
    raw = dict(zip(s["columns"], raw))
    shown_numbers = [v for v in row.values() if isinstance(v, int)]
    for a in shown_numbers:
        for b in shown_numbers:
            assert not (0 < a - b < privacy.THRESHOLD), (a, b)
    assert raw["patients"] - raw["with_survival"] < privacy.THRESHOLD
    assert row["with_survival"] == privacy.SECONDARY


def test_within_row_complement_generic_query():
    r = run_sql("SELECT COUNT(*), SUM(has_rna_tumor) FROM data_availability")
    a, b = r["rows"][0]
    assert b == privacy.SECONDARY  # 1092 vs 1090 differs by 2


GOLD_ITEMS = [it for f in ("gold.yaml", "stress.yaml")
              for it in yaml.safe_load((ROOT / "gold" / f).read_text())["items"] if it.get("sql")]


@pytest.mark.parametrize("item", GOLD_ITEMS, ids=lambda it: it["id"])
def test_shown_result_equals_scored_result_modulo_suppression(item):
    """Users see the result of the qualified/rewritten SQL; the harness scores the original SQL.
    They must agree on every value that is not suppressed."""
    raw = run_sql_raw(item["sql"]).rows
    shown = run_sql(item["sql"])
    assert shown["ok"], shown.get("error")
    assert len(raw) == len(shown["rows"])
    for r_raw, r_shown in zip(raw, shown["rows"]):
        for a, b in zip(r_raw, r_shown):
            assert b in privacy.BLANKED or a == b, (item["id"], r_raw, r_shown)
