"""Gold-set evaluation harness: execution accuracy + refusal accuracy + failure taxonomy.

Run: python -m cohort.evaluate --backend azure --split dev [--tag run1]

Execution accuracy compares the RAW result (before small-cell suppression) of the final SQL
with the RAW result of the reference SQL: order-insensitive unless the item is `ordered`,
column-name-insensitive (any column permutation), numbers compared after rounding to 2 dp.
"strict" requires the same number of columns; "lenient" also accepts extra predicted columns.
"""
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

from cohort.catalog import ROOT
from cohort.nl2sql import PROMPT_VERSION, ask, get_backend
from cohort.service import run_sql_raw
from cohort.sqlguard import GuardError

GOLD = ROOT / "gold" / "gold.yaml"


def _norm(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    return v


def _key(row):
    return tuple((0, "") if v is None else (1, v) if isinstance(v, float) else (2, str(v)) for v in row)


def _same(a: list[tuple], b: list[tuple], ordered: bool) -> bool:
    if ordered:
        return a == b
    return sorted(a, key=_key) == sorted(b, key=_key)


def results_match(pred: list, ref: list, ordered: bool = False, lenient: bool = False) -> bool:
    pred = [tuple(_norm(v) for v in r) for r in pred]
    ref = [tuple(_norm(v) for v in r) for r in ref]
    if len(pred) != len(ref):
        return False
    if not ref:
        return True
    n_ref, n_pred = len(ref[0]), len(pred[0])
    if n_pred < n_ref or (n_pred > n_ref and not lenient):
        return False
    if n_pred > 8:
        return False
    for perm in itertools.permutations(range(n_pred), n_ref):
        if _same([tuple(r[i] for i in perm) for r in pred], ref, ordered):
            return True
    return False


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "describe", "--always", "--dirty"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def evaluate(backend_name: str, split: str, tag: str = "run1", role: str = "researcher") -> dict:
    items = [it for it in yaml.safe_load(GOLD.read_text())["items"] if split in ("all", it["split"])]
    backend = get_backend(backend_name)
    rows = []
    for it in items:
        resp = ask(it["question"], role=role, backend=backend)
        rec = {"id": it["id"], "split": it["split"], "kind": it["kind"], "question": it["question"],
               "outcome": resp["outcome"], "repaired": resp["repaired"],
               "attempts": [{k: a.get(k) for k in ("sql", "refuse", "reason", "guard_error", "usage", "cached")}
                            for a in resp["attempts"]],
               "final_sql": resp["sql"], "reference_sql": it.get("sql")}
        if it["kind"] == "answer":
            ref = run_sql_raw(it["sql"]).rows
            rec["reference_result"] = ref
            if resp["outcome"] == "valid":
                try:
                    pred = run_sql_raw(resp["sql"]).rows
                except GuardError as e:
                    pred, rec["outcome"] = None, f"execution_error: {e.message}"
                rec["predicted_result"] = pred
                if pred is not None:
                    rec["correct"] = results_match(pred, ref, it.get("ordered", False))
                    rec["correct_lenient"] = results_match(pred, ref, it.get("ordered", False), lenient=True)
                    rec["shown_to_user"] = resp["result"].get("rows")
                    rec["suppressed_cells"] = resp["result"].get("suppressed_cells")
                else:
                    rec["correct"] = rec["correct_lenient"] = False
            else:
                rec["correct"] = rec["correct_lenient"] = False
        else:
            rec["correct"] = resp["outcome"] in ("model_refused", "guard_rejected")
            rec["blocked_by"] = {"model_refused": "model", "guard_rejected": "guard"}.get(resp["outcome"])
        rows.append(rec)

    ans = [r for r in rows if r["kind"] == "answer"]
    ref_items = [r for r in rows if r["kind"] == "refuse"]
    usage = Counter()
    n_calls = n_cached = 0
    for r in rows:
        for a in r["attempts"]:
            if a.get("usage"):
                n_calls += 1
                n_cached += bool(a.get("cached"))
                usage.update(a["usage"])
    summary = {
        "backend": backend.label, "prompt_version": PROMPT_VERSION, "split": split, "tag": tag, "role": role,
        "git_commit": _git_commit(), "run_at": datetime.now(timezone.utc).isoformat(),
        "n_answer_items": len(ans),
        "execution_accuracy": round(sum(r["correct"] for r in ans) / len(ans), 4) if ans else None,
        "n_correct": sum(r["correct"] for r in ans),
        "execution_accuracy_lenient": round(sum(r["correct_lenient"] for r in ans) / len(ans), 4) if ans else None,
        "n_refuse_items": len(ref_items),
        "refusal_accuracy": round(sum(r["correct"] for r in ref_items) / len(ref_items), 4) if ref_items else None,
        "n_refused_correctly": sum(r["correct"] for r in ref_items),
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "first_attempt_guard_rejections": sum(1 for r in rows if r["attempts"][0].get("guard_error")),
        "repaired_then_correct": sum(1 for r in rows if r["repaired"] and r["correct"]),
        "silent_wrong_answers": [r["id"] for r in ans if r["outcome"] == "valid" and not r["correct"]],
        "llm_calls": n_calls, "llm_calls_from_cache": n_cached, "tokens": dict(usage),
    }
    out = {"summary": summary, "items": rows}
    name = f"eval_{backend.name}_{split}_{tag}.json"
    (ROOT / "results" / name).write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps(summary, indent=2))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="auto", choices=["auto", "azure", "template"])
    ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    ap.add_argument("--tag", default="run1")
    a = ap.parse_args()
    evaluate(a.backend, a.split, a.tag)


if __name__ == "__main__":
    main()
