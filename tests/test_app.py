"""API smoke tests through FastAPI's TestClient (template backend only; no network)."""
import pytest
from fastapi.testclient import TestClient

from cohort.app import app

pytestmark = pytest.mark.needs_db
client = TestClient(app)


def test_index_and_catalog():
    assert client.get("/").status_code == 200
    cat = client.get("/api/catalog").json()
    assert {t["name"] for t in cat["tables"]} >= {"patients", "clinical", "slide_images"}


def test_search():
    hits = client.get("/api/catalog/search", params={"q": "slide"}).json()["hits"]
    assert any(h["table"] == "slide_images" for h in hits)


def test_cohort_endpoint():
    r = client.post("/api/cohort", json={"filters": {"er_status": ["Positive"]}}).json()
    assert r["ok"] and r["summary"]["rows"][0][0] > 11


def test_sql_endpoint_rejects_restricted_for_researcher():
    r = client.post("/api/sql", json={"sql": "SELECT report_text FROM pathology_reports"}).json()
    assert not r["ok"] and r["error_stage"] == "access_tier"


def test_export_blocks_free_text_and_row_level():
    r = client.post("/api/export", json={"sql": "SELECT COUNT(*) FROM pathology_reports WHERE report_text LIKE '%x%'",
                                         "role": "approved"})
    assert r.status_code == 403
    r = client.post("/api/export", json={"sql": "SELECT patient_id FROM patients", "role": "approved"})
    assert r.status_code == 403


def test_export_aggregate_csv_is_suppressed():
    r = client.post("/api/export", json={"sql": "SELECT race, COUNT(*) FROM patients GROUP BY race"})
    assert r.status_code == 200 and "<11" in r.text and ",1\n" not in r.text


def test_ask_template_backend():
    r = client.post("/api/ask", json={"question": "How many male patients are there?", "backend": "template"}).json()
    assert r["outcome"] == "valid" and r["result"]["executed_sql"]


def test_bad_role():
    assert client.post("/api/sql", json={"sql": "SELECT COUNT(*) FROM patients", "role": "admin"}).status_code == 400


def test_ask_llm_backend_failure_returns_clean_502(monkeypatch, tmp_path):
    """A provider failure (here: HTTP 429 insufficient quota) must not surface as an unhandled 500."""
    from types import SimpleNamespace

    import httpx
    import openai

    from cohort import nl2sql

    class Failing:
        def create(self, **kw):
            resp = httpx.Response(429, request=httpx.Request("POST", "https://llm.invalid/v1/chat/completions"))
            raise openai.RateLimitError("no credit", response=resp, body=None)

    b = object.__new__(nl2sql.OpenAILLM)
    nl2sql._ChatLLM.__init__(b)
    b.cache_model = b.call_model = "m"
    b.label = "failing test backend"
    b.client = SimpleNamespace(chat=SimpleNamespace(completions=Failing()))
    monkeypatch.setattr(nl2sql, "CACHE", tmp_path / "cache.jsonl")
    monkeypatch.setattr(nl2sql, "get_backend", lambda prefer="auto", variant=nl2sql.PROMPT_VERSION: b)
    r = client.post("/api/ask", json={"question": "How many patients?"})
    assert r.status_code == 502
    assert "RateLimitError" in r.json()["detail"] and "429" in r.json()["detail"]
