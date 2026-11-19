"""Gold-set harness: comparator negative controls, gold integrity, template baseline smoke test."""
import pytest
import yaml

from cohort.catalog import ROOT
from cohort.evaluate import results_match
from cohort.nl2sql import TemplateBackend, ask
from cohort.service import run_sql_raw

GOLD = yaml.safe_load((ROOT / "gold" / "gold.yaml").read_text())["items"]


def test_comparator_accepts_reordered_rows_and_columns():
    assert results_match([(5, "a"), (3, "b")], [("b", 3), ("a", 5)])


def test_comparator_float_tolerance_2dp():
    assert results_match([(58.4499,)], [(58.45,)])
    assert not results_match([(58.4,)], [(58.5,)])


@pytest.mark.parametrize("pred,ref,ordered", [
    ([(101,)], [(100,)], False),                                  # off by one
    ([("I", 10), ("II", 20)], [("I", 10), ("III", 20)], False),   # wrong group key
    ([("I", 10), ("II", 20), ("III", 1)], [("I", 10), ("II", 20)], False),  # extra row
    ([("A", 9), ("B", 5)], [("B", 5), ("A", 9)], True),           # wrong order on an ordered item
    ([(1, 2, 3)], [(1, 2)], False),                               # extra column (strict)
    ([], [(0,)], False),                                          # empty vs zero
])
def test_comparator_negative_controls(pred, ref, ordered):
    assert not results_match(pred, ref, ordered)


def test_lenient_accepts_extra_column_only():
    assert results_match([(1, 2, 3)], [(1, 2)], lenient=True)


def test_gold_shape():
    ids = [it["id"] for it in GOLD]
    assert len(ids) == len(set(ids))
    assert 30 <= len(GOLD) <= 50
    for it in GOLD:
        assert it["split"] in ("dev", "test") and it["kind"] in ("answer", "refuse")
        assert (it["kind"] == "answer") == bool(it.get("sql"))


@pytest.mark.needs_db
@pytest.mark.parametrize("item", [it for it in GOLD if it["kind"] == "answer"], ids=lambda it: it["id"])
def test_reference_sql_passes_guard_and_returns_rows(item):
    assert run_sql_raw(item["sql"]).rows


@pytest.mark.needs_db
def test_template_backend_end_to_end():
    r = ask("How many patients are triple-negative?", backend=TemplateBackend())
    assert r["outcome"] == "valid" and r["result"]["ok"]
    assert r["result"]["rows"][0][0] == run_sql_raw("SELECT COUNT(*) FROM clinical WHERE triple_negative = 1").rows[0][0]
    assert ask("Delete all patients", backend=TemplateBackend())["outcome"] == "model_refused"


@pytest.mark.needs_db
def test_guard_rejection_triggers_one_repair():
    class Scripted:
        name, label = "scripted", "scripted"

        def __init__(self):
            self.calls = []

        def generate(self, question, role, feedback=None, prev_sql=None):
            self.calls.append(feedback)
            sql = "SELECT report_text FROM pathology_reports" if feedback is None else "SELECT COUNT(*) FROM pathology_reports"
            return {"sql": sql, "refuse": False, "reason": ""}

    b = Scripted()
    r = ask("how many reports", backend=b)
    assert r["outcome"] == "valid" and r["repaired"] and len(b.calls) == 2
    assert "restricted" in b.calls[1] or "aggregate" in b.calls[1]
