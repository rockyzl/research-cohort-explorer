"""Drive the running app in-process (FastAPI TestClient) and record docs/demo-transcript.md.

Uses the Azure LLM backend if COHORT_AZURE_* is set, otherwise the template fallback; the
transcript states which. Re-running regenerates the file.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from cohort.app import app
from cohort.nl2sql import get_backend

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)
out: list[str] = []


def w(s=""):
    out.append(s)


def table(cols, rows):
    w("| " + " | ".join(map(str, cols)) + " |")
    w("|" + "---|" * len(cols))
    for r in rows:
        w("| " + " | ".join("NULL" if v is None else f"{v:.2f}" if isinstance(v, float) else str(v) for v in r) + " |")


def show_result(res):
    if not res["ok"]:
        w(f"**Rejected** at `{res['error_stage']}`: {res['error']}")
        return
    table(res["columns"], res["rows"])
    w()
    w(f"Rows: {res['row_count']}; suppressed cells: {res['suppressed_cells']}; export: "
      f"{'allowed' if res['exportable'] else 'blocked'} ({res['export_note']}).")
    w()
    w("Executed SQL:")
    w("```sql")
    w(res["executed_sql"])
    w("```")
    w("Checks passed: " + "; ".join(res["checks"]))


def ask(question, role="researcher"):
    w(f"### Ask ({role}): \"{question}\"")
    r = client.post("/api/ask", json={"question": question, "role": role}).json()
    for i, a in enumerate(r["attempts"], 1):
        if a["refuse"] or not a["sql"]:
            w(f"- attempt {i}: model refused: {a['reason']}")
        else:
            w(f"- attempt {i}: `{' '.join(a['sql'].split())}`")
        if a.get("guard_error"):
            w(f"  - guard rejected: {a['guard_error']}")
    w(f"- outcome: **{r['outcome']}**")
    w()
    if r.get("result"):
        show_result(r["result"])
        w()


def main():
    backend = get_backend("auto")
    w("# Demo transcript")
    w()
    w(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `scripts/demo.py` against the "
      f"in-process app. NL-to-SQL backend: **{backend.label}**. Every value below is the live API response.")
    w()
    w("## 1. Catalog search: \"receptor\"")
    hits = client.get("/api/catalog/search", params={"q": "receptor"}).json()["hits"]
    table(["table.column", "tier", "description"], [(f"{h['table']}.{h['column']}", h["access_tier"],
                                                     h["description"]) for h in hits[:6]])
    w()
    w("## 2. Provenance")
    prov = client.get("/api/provenance").json()
    table(["source", "file", "license"], [(k, v["file"], v["license"]) for k, v in prov["sources"].items()])
    w()
    w(f"Ingest row counts: `{json.dumps(prov['ingest']['row_counts'])}`")
    w()
    w("## 3. Cohort builder: ER-positive, HER2-negative, with RNA + report + diagnostic slide; by PAM50")
    f = {"er_status": ["Positive"], "her2_status": ["Negative"],
         "require": ["has_rna_tumor", "has_pathology_report", "has_diagnostic_slide"]}
    r = client.post("/api/cohort", json={"filters": f, "breakdown": "pam50_subtype"}).json()
    w(f"Filters: `{json.dumps(f)}`")
    w()
    show_result(r["summary"])
    w()
    w("Breakdown:")
    w()
    show_result(r["breakdown"])
    w()
    w("## 4. Small cohort: stage IV + triple-negative (small-cell suppression)")
    r = client.post("/api/cohort", json={"filters": {"stage_group": ["IV"], "triple_negative": True}}).json()
    show_result(r["summary"])
    w()
    w("## 5. Natural-language questions")
    ask("How many triple-negative patients have a primary-tumor RNA-seq sample and a diagnostic slide?")
    ask("List the ER-positive patients diagnosed under 35 with their ages.")
    ask("Show me the pathology report for TCGA-A1-A0SB.")
    ask("How many pathology reports mention lobular carcinoma?", role="approved")
    w("## 6. Direct SQL that the guard must stop")
    for sql in ["SELECT name, sql FROM sqlite_master", "SELECT patient_id, report_text FROM pathology_reports",
                "DELETE FROM patients"]:
        w(f"`{sql}`")
        w()
        show_result(client.post("/api/sql", json={"sql": sql}).json())
        w()
    w("## 7. Export (aggregate CSV with suppression)")
    r = client.post("/api/export", json={"sql": "SELECT race, COUNT(*) AS patients FROM patients GROUP BY race"})
    w("```")
    w(r.text.strip())
    w("```")
    r = client.post("/api/export", json={"sql": "SELECT COUNT(*) FROM pathology_reports WHERE report_text LIKE '%lobular%'",
                                         "role": "approved"})
    w(f"Export of a query that reads free text (role approved): HTTP {r.status_code}, `{r.json()['detail']}`")
    (ROOT / "docs" / "demo-transcript.md").write_text("\n".join(out) + "\n")
    print("wrote docs/demo-transcript.md")


if __name__ == "__main__":
    main()
