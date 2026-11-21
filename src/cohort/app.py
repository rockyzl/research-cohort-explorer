"""FastAPI app: catalog browser, structured cohort builder, guarded NL-to-SQL.

Run: uvicorn cohort.app:app --port 8790   (or `make serve`)

Roles are SIMULATED via a request field (no real authentication): "researcher" (default,
aggregate-only, public tier) and "approved" (row-level + restricted text, still no free-text export).
Every query is appended to data/audit.jsonl (gitignored).
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from cohort import nl2sql
from cohort.catalog import ROLES, ROOT, load_catalog
from cohort.service import FILTER_FIELDS, REQUIRE_FLAGS, build_cohort, run_sql

STATIC = Path(__file__).parent / "static"
AUDIT = ROOT / "data" / "audit.jsonl"

app = FastAPI(title="TCGA-BRCA Research Cohort Explorer (prototype)")


def _audit(kind: str, role: str, payload: dict, ok: bool, detail: str | None = None) -> None:
    rec = {"at": datetime.now(timezone.utc).isoformat(), "kind": kind, "role": role, "ok": ok,
           "detail": detail, **payload}
    with AUDIT.open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def _role(role: str) -> str:
    if role not in ROLES:
        raise HTTPException(400, f"role must be one of {ROLES}")
    return role


class CohortReq(BaseModel):
    filters: dict = Field(default_factory=dict)
    breakdown: str | None = "stage_group"
    role: str = "researcher"


class SqlReq(BaseModel):
    sql: str
    role: str = "researcher"


class AskReq(BaseModel):
    question: str
    role: str = "researcher"
    backend: str = "auto"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/catalog")
def catalog():
    cat = load_catalog()
    return {"dataset": cat.raw["dataset"], "relationships": cat.raw["relationships"],
            "tables": [{"name": t, **{k: spec[k] for k in ("description", "grain", "primary_key", "access_tier",
                                                              "source", "omop_like")},
                        "columns": [{"name": c, "type": col.type, "description": col.description, "unit": col.unit,
                                     "allowed_values": col.allowed_values, "access_tier": col.access_tier,
                                     "free_text": col.free_text, "source": col.source, "omop_like": col.omop_like}
                                    for c, col in cat.columns[t].items()]}
                       for t, spec in cat.tables.items()]}


@app.get("/api/catalog/search")
def search(q: str):
    return {"query": q, "hits": load_catalog().search(q)}


@app.get("/api/provenance")
def provenance():
    manifest = json.loads((ROOT / "data" / "manifest.json").read_text())
    stats = json.loads((ROOT / "results" / "ingest_stats.json").read_text())
    return {"sources": {k: {f: v.get(f) for f in ("file", "url", "license", "citation", "retrieved_at", "sha256")}
                        for k, v in manifest["sources"].items()},
            "ingest": stats}


@app.get("/api/options")
def options():
    cat = load_catalog()
    return {"filters": {k: list(cat.columns[t][c].allowed_values) for k, (t, c) in FILTER_FIELDS.items()},
            "require": REQUIRE_FLAGS, "roles": list(ROLES),
            "llm_available": nl2sql.llm_available()}


@app.post("/api/cohort")
def cohort(req: CohortReq):
    role = _role(req.role)
    out = build_cohort(req.filters, role, req.breakdown)
    _audit("cohort", role, {"filters": req.filters, "sql": out.get("summary", {}).get("sql")}, out["ok"],
           out.get("error"))
    return out


@app.post("/api/sql")
def sql(req: SqlReq):
    role = _role(req.role)
    out = run_sql(req.sql, role)
    _audit("sql", role, {"sql": req.sql}, out["ok"], out.get("error"))
    return out


@app.post("/api/ask")
def ask(req: AskReq):
    role = _role(req.role)
    try:
        backend = nl2sql.get_backend(req.backend)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from None
    try:
        out = nl2sql.ask(req.question, role, backend)
    except nl2sql.LLMBackendError as e:
        _audit("ask", role, {"question": req.question, "sql": None, "backend": backend.label}, False,
               f"llm_backend_error: {e}")
        raise HTTPException(502, f"{e}. Retry later, or ask with backend 'template'.") from None
    _audit("ask", role, {"question": req.question, "sql": out.get("sql"), "backend": out["backend"]},
           out["outcome"] == "valid", out["outcome"])
    return out


@app.post("/api/export", response_class=PlainTextResponse)
def export(req: SqlReq):
    role = _role(req.role)
    out = run_sql(req.sql, role)
    if not out["ok"]:
        _audit("export", role, {"sql": req.sql}, False, out["error"])
        raise HTTPException(400, out["error"])
    if not out["exportable"]:
        _audit("export", role, {"sql": req.sql}, False, out["export_note"])
        raise HTTPException(403, out["export_note"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(out["columns"])
    w.writerows(out["rows"])
    buf.write(f"# small-cell suppression: values 1-10 shown as <11; suppressed cells: {out['suppressed_cells']}\n")
    buf.write(f"# sql: {out['executed_sql']}\n")
    _audit("export", role, {"sql": req.sql}, True)
    return buf.getvalue()
