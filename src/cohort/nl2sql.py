"""Natural-language question -> SQL, with the guard in the loop.

Backends:
  * AzureLLM  - Azure OpenAI GPT-4.1 deployment (endpoint from env), keyless auth via the local Azure CLI
                (same pattern as oncology-multimodal-lab/assistant/provider.py). No API key is read,
                printed or stored. Every call is cached in results/llm_cache.jsonl (gitignored) and
                its token usage logged.
  * Template  - deterministic rule-based fallback; no network. Used when no credential is
                available and as a baseline in the evaluation.

Pipeline: generate -> guard.validate; if the guard rejects the SQL, the LLM gets ONE repair attempt
with the guard's error message; the second rejection is final. The response always carries the
executed SQL, the checks it passed, and result counts.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from cohort.catalog import ROOT, load_catalog
from cohort.service import run_sql
from cohort.sqlguard import GuardError, validate

CACHE = ROOT / "results" / "llm_cache.jsonl"
PROMPT_VERSION = "v1"

# Ablation prompts (evaluation only):
#  bare       - schema only, none of the domain rules below (measures how much the hand-written
#               rules, authored with knowledge of the gold set, contribute).
#  permissive - schema rendered for role "approved" (so restricted columns are visible), no refusal
#               rules, always answer; the SQL is still validated/executed as "researcher". This
#               isolates what the guard alone catches when the generator does not self-censor.
BARE_RULES = """Translate the question into one SQLite query over the schema below.
Output JSON: {"sql": "<query or empty>", "refuse": true|false, "reason": "<short>"}."""
PERMISSIVE_RULES = """Translate the question into one SQLite SELECT query over the schema below.
Always produce a query that answers the question as directly as possible; never refuse.
Output JSON: {"sql": "<query>", "refuse": false, "reason": "<short>"}."""

SYSTEM_RULES = """You translate a researcher's question into ONE SQLite SELECT query over the schema below.
Rules:
- Output JSON: {"sql": "<query or empty>", "refuse": true|false, "reason": "<short>"}.
- Use only the tables and columns listed. Never invent columns or values; use the listed values exactly (case-sensitive).
- Read-only: a single SELECT (CTEs allowed). Never modify data. No PRAGMA, ATTACH or sqlite_master.
- Refuse (refuse=true, sql="") if the question needs data that is not in the schema (e.g. names, dates of birth, addresses, hospitals not listed), asks to change data, or asks for a column that is not listed for your role.
- "Patients" means distinct patient_id. When counting patients from a table whose grain is not patient (rna_samples, gene_expression, slide_images), use COUNT(DISTINCT patient_id).
- For HER2-positive/negative use clinical.her2_status unless the question names IHC or ISH explicitly.
- For "stage I/II/III/IV" use clinical.stage_group unless a sub-stage such as IIA is named.
- Gene expression questions about tumors use primary-tumor samples (rna_samples.sample_type_code = '01').
- Exclude NULLs from GROUP BY outputs when the question says "excluding missing".
- Return only the columns the question asks for, in the order asked. Do not add LIMIT unless the question asks for top-N.
"""


# ------------------------------------------------------------------------------------------------
# Azure OpenAI backend
# ------------------------------------------------------------------------------------------------
_lock = threading.Lock()
_token = {"value": None, "expires": 0.0}


def _access_token() -> str:
    with _lock:
        if _token["expires"] < time.time() + 120:
            sub = os.environ["COHORT_AZURE_SUBSCRIPTION"]
            r = subprocess.run(["az", "account", "get-access-token", "--subscription", sub,
                                "--resource", "https://cognitiveservices.azure.com/", "-o", "json"],
                               capture_output=True, text=True, timeout=30, check=True)
            d = json.loads(r.stdout)
            _token.update(value=d["accessToken"], expires=float(d.get("expires_on", time.time() + 600)))
        return _token["value"]


class AzureLLM:
    name = "azure-openai"

    def __init__(self, variant: str = PROMPT_VERSION):
        from openai import AzureOpenAI
        if variant not in (PROMPT_VERSION, "bare", "permissive"):
            raise ValueError(variant)
        self.variant = variant
        self.endpoint = os.environ["COHORT_AZURE_ENDPOINT"]
        self.deployment = os.environ.get("COHORT_AZURE_DEPLOYMENT", "gpt-4.1")
        self.api_version = "2024-10-21"
        self.client = AzureOpenAI(azure_endpoint=self.endpoint, azure_ad_token_provider=_access_token,
                                  api_version=self.api_version, timeout=60, max_retries=1)
        self.label = (f"Azure OpenAI deployment '{self.deployment}' (api {self.api_version}, temperature 0, "
                      f"prompt {variant})")

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("COHORT_AZURE_ENDPOINT") and os.environ.get("COHORT_AZURE_SUBSCRIPTION"))

    def complete(self, messages: list[dict]) -> dict:
        key = hashlib.sha256(json.dumps([self.deployment, messages], sort_keys=True).encode()).hexdigest()
        if CACHE.exists():
            with CACHE.open() as f:
                for line in f:
                    rec = json.loads(line)
                    if rec["key"] == key:
                        return {**rec["response"], "cached": True}
        t0 = time.monotonic()
        r = self.client.chat.completions.create(model=self.deployment, messages=messages, temperature=0,
                                                max_tokens=700, response_format={"type": "json_object"})
        content = r.choices[0].message.content or "{}"
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = {"sql": "", "refuse": False, "reason": "unparseable model output", "raw": content}
        resp = {"parsed": parsed, "usage": {"input_tokens": r.usage.prompt_tokens,
                                            "output_tokens": r.usage.completion_tokens},
                "seconds": round(time.monotonic() - t0, 2), "model": r.model,
                "finish_reason": r.choices[0].finish_reason}
        CACHE.parent.mkdir(exist_ok=True)
        with CACHE.open("a") as f:
            f.write(json.dumps({"key": key, "messages": messages, "response": resp}) + "\n")
        return {**resp, "cached": False}

    def generate(self, question: str, role: str, feedback: str | None = None, prev_sql: str | None = None) -> dict:
        if self.variant == "permissive":
            system = f"{PERMISSIVE_RULES}\n\nSCHEMA\n{load_catalog().as_prompt('approved')}"
        elif self.variant == "bare":
            system = f"{BARE_RULES}\n\nSCHEMA\n{load_catalog().as_prompt(role)}"
        else:
            system = f"{SYSTEM_RULES}\nYour role: {role}\n\nSCHEMA\n{load_catalog().as_prompt(role)}"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": question}]
        if feedback:
            messages += [{"role": "assistant", "content": json.dumps({"sql": prev_sql, "refuse": False})},
                         {"role": "user", "content": f"The query was rejected by the validator: {feedback}\n"
                                                     "Return a corrected JSON answer, or refuse if the question "
                                                     "cannot be answered with the allowed schema."}]
        resp = self.complete(messages)
        p = resp["parsed"]
        return {"sql": (p.get("sql") or "").strip(), "refuse": bool(p.get("refuse")), "reason": p.get("reason", ""),
                "usage": resp["usage"], "cached": resp["cached"], "model": resp["model"]}


# ------------------------------------------------------------------------------------------------
# Deterministic template fallback
# ------------------------------------------------------------------------------------------------
class TemplateBackend:
    """Keyword rules -> a COUNT(DISTINCT patient) query with WHERE filters and optional GROUP BY.

    Deliberately simple: it covers single-cohort counts and one-dimension breakdowns. Anything it
    does not recognise it refuses rather than guesses.
    """
    name = "template"
    label = "deterministic keyword-template fallback (no LLM)"

    REFUSE_WORDS = ("delete", "update ", "drop", "insert", "name", "birth", "address", "sqlite_master",
                    "schema", "report text", "text of", "full pathology report")
    GROUPS = {"pam50": "c.pam50_subtype", "stage group": "c.stage_group", "race": "p.race",
              "menopause": "p.menopause_status", "histolog": "c.histological_type",
              "tissue source site": "p.tissue_source_site"}

    def generate(self, question: str, role: str, feedback: str | None = None, prev_sql: str | None = None) -> dict:
        q = question.lower()
        if any(w in q for w in self.REFUSE_WORDS):
            return {"sql": "", "refuse": True, "reason": "template: disallowed or unavailable request"}
        if not re.search(r"\b(how many|count|number of)\b", q):
            return {"sql": "", "refuse": True, "reason": "template: only count questions are supported"}
        w = []
        pairs = [
            (r"\ber[- ]positive", "c.er_status = 'Positive'"), (r"\ber[- ]negative", "c.er_status = 'Negative'"),
            (r"\bpr[- ]positive", "c.pr_status = 'Positive'"), (r"\bpr[- ]negative", "c.pr_status = 'Negative'"),
            (r"her2[- ]positive", "c.her2_status = 'Positive'"), (r"her2[- ]negative", "c.her2_status = 'Negative'"),
            (r"triple[- ]negative", "c.triple_negative = 1"),
            (r"\bmale\b", "p.gender = 'MALE'"), (r"\bfemale\b", "p.gender = 'FEMALE'"),
            (r"\bdied\b|\bdead\b", "c.vital_status = 'Dead'"),
            (r"lobular", "c.histological_type = 'Infiltrating Lobular Carcinoma'"),
            (r"\bbasal\b", "c.pam50_subtype = 'Basal'"),
            (r"pathology report", "a.has_pathology_report = 1"),
            (r"diagnostic slide", "a.has_diagnostic_slide = 1"),
            (r"solid[- ]tissue[- ]normal", "a.has_rna_normal = 1"),
            (r"primary[- ]tumou?r rna|rna-seq", "a.has_rna_tumor = 1"),
            (r"pfi event|progression", "c.pfi_event = 1"),
        ]
        for pat, cond in pairs:
            if re.search(pat, q):
                w.append(cond)
        m = re.search(r"stage (iv|iii|ii|i)\b", q)
        if m and "stage group" not in q:
            w.append(f"c.stage_group = '{m.group(1).upper()}'")
        m = re.search(r"(?:age|aged) (\d+) or (older|younger)", q) or re.search(r"at age (\d+) or (younger|older)", q)
        if m:
            w.append(f"p.age_at_diagnosis {'>=' if m.group(2) == 'older' else '<='} {int(m.group(1))}")
        m = re.search(r"missing (er|pr) status", q)
        if m:
            w.append(f"c.{m.group(1)}_status IS NULL")
        group = next((col for k, col in self.GROUPS.items() if re.search(rf"\bby {k}", q)), None)
        sql = "SELECT "
        sql += f"{group}, COUNT(DISTINCT p.patient_id) AS patients " if group else "COUNT(DISTINCT p.patient_id) AS patients "
        sql += ("FROM patients p JOIN clinical c ON c.patient_id = p.patient_id "
                "JOIN data_availability a ON a.patient_id = p.patient_id")
        if group and "excluding missing" in q:
            w.append(f"{group} IS NOT NULL")
        if w:
            sql += " WHERE " + " AND ".join(w)
        if group:
            sql += f" GROUP BY {group}"
        return {"sql": sql, "refuse": False, "reason": "template match"}


def get_backend(prefer: str = "auto", variant: str = PROMPT_VERSION):
    if prefer == "template":
        return TemplateBackend()
    if prefer in ("auto", "azure") and AzureLLM.available():
        return AzureLLM(variant)
    if prefer == "azure":
        raise RuntimeError("Azure backend requested but COHORT_AZURE_ENDPOINT/SUBSCRIPTION are not set")
    return TemplateBackend()


def _check(gen: dict, role: str) -> str:
    if gen["refuse"] or not gen["sql"]:
        return "model_refused"
    try:
        validate(gen["sql"], role)
        return "valid"
    except GuardError as e:
        gen["guard_error"] = f"[{e.stage}] {e.message}"
        return "guard_rejected"


def ask(question: str, role: str = "researcher", backend=None, repair: bool = True) -> dict:
    """Question -> guarded SQL -> policy-checked answer. Returns a trace of every attempt."""
    backend = backend or get_backend()
    gen = backend.generate(question, role)
    attempts = [gen]
    outcome = _check(gen, role)
    if outcome == "guard_rejected" and repair and backend.name != "template":
        gen = backend.generate(question, role, feedback=gen["guard_error"], prev_sql=gen["sql"])
        attempts.append(gen)
        outcome = _check(gen, role)
    resp = {"question": question, "role": role, "backend": backend.label, "outcome": outcome,
            "attempts": attempts, "sql": gen.get("sql", ""), "repaired": len(attempts) > 1}
    if outcome == "valid":
        resp["result"] = run_sql(gen["sql"], role)
        if not resp["result"]["ok"]:
            resp["outcome"] = "execution_error"
    return resp
