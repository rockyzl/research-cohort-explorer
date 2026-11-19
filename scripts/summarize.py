"""Collect every saved result into results/summary.json and print the README tables.

All numbers quoted in README.md / docs come from this file's output.
"""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"

RUNS = {
    "llm_v1_dev": "eval_azure-openai_dev_prompt_v1.json",
    "llm_v1_test": "eval_azure-openai_test_frozen_v1.json",
    "llm_v1_stress": "eval_azure-openai_stress_frozen_v1.json",
    "llm_bare_everything": "eval_azure-openai_bare_everything_ablation.json",
    "llm_permissive_everything": "eval_azure-openai_permissive_everything_ablation.json",
    "template_all": "eval_template_all_baseline.json",
}


def subset(items, pred):
    sel = [r for r in items if pred(r)]
    ans = [r for r in sel if r["kind"] in ("answer", "either")]
    ref = [r for r in sel if r["kind"] == "refuse"]
    return {"answer_correct": sum(r["correct"] for r in ans), "answer_n": len(ans),
            "answer_correct_lenient": sum(r.get("correct_lenient", False) for r in ans),
            "refuse_correct": sum(r["correct"] for r in ref), "refuse_n": len(ref)}


BLANKED = ("<11", "suppressed")


def shown_vs_scored(items):
    """Items where the result shown to the user (qualified/rewritten SQL, after the output policy)
    differs from the raw result the harness scored, on any non-suppressed value."""
    bad = []
    for r in items:
        pred, shown = r.get("predicted_result"), r.get("shown_to_user")
        if pred is None or shown is None:
            continue
        same = len(pred) == len(shown) and all(
            len(a) == len(b) and all(y in BLANKED or x == y for x, y in zip(a, b)) for a, b in zip(pred, shown))
        if not same:
            bad.append(r["id"])
    return bad


def main():
    out = {"runs": {}}
    for key, fname in RUNS.items():
        p = RES / fname
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        s, items = d["summary"], d["items"]
        rec = {"file": fname, "backend": s["backend"], "git_commit": s["git_commit"], "run_at": s["run_at"],
               "llm_calls": s["llm_calls"], "llm_calls_from_cache": s["llm_calls_from_cache"], "tokens": s["tokens"],
               "outcomes": s["outcomes"], "silent_wrong_answers": s["silent_wrong_answers"],
               "first_attempt_guard_errors": s.get("first_attempt_guard_errors", {}),
               "shown_vs_scored_mismatches": shown_vs_scored(items),
               "n_items_shown_and_scored": sum(1 for r in items if r.get("shown_to_user") is not None),
               "by_split": {sp: subset(items, lambda r, sp=sp: r["split"] == sp)
                            for sp in sorted({r["split"] for r in items})}}
        out["runs"][key] = rec

    perm = json.loads((RES / RUNS["llm_permissive_everything"]).read_text())["items"]
    stages = Counter()
    unsafe_ids = []
    for r in perm:
        for a in r["attempts"]:
            if a.get("guard_error"):
                stage = re.match(r"\[(\w+)\]", a["guard_error"]).group(1)
                stages[stage] += 1
                if stage in ("access_tier", "read_only", "table_allowlist"):
                    unsafe_ids.append(r["id"])
    out["guard_ablation"] = {
        "description": "permissive generator (sees restricted columns, told never to refuse); SQL validated as researcher",
        "rejections_by_stage": dict(stages),
        "items_with_unsafe_attempt": sorted(set(unsafe_ids)),
        "unsafe_attempts_total": sum(stages[s] for s in ("access_tier", "read_only", "table_allowlist")),
        "unsafe_attempts_executed": 0,
        "note": "executed=0 holds by construction (only validated SQL runs) and is re-checked by tests/test_engine_layer.py",
    }
    gv = json.loads((RES / "gold_verification.json").read_text())
    out["gold_verification"] = {"n_items": gv["n_items"], "n_agree": gv["n_agree"]}
    out["ingest"] = json.loads((RES / "ingest_stats.json").read_text())
    calls = sum(r["llm_calls"] - r["llm_calls_from_cache"] for r in out["runs"].values())
    out["llm_calls_in_saved_runs_not_from_cache"] = calls
    (RES / "summary.json").write_text(json.dumps(out, indent=2) + "\n")

    print("| run | split | answer exec. acc. (strict) | lenient | refusal acc. |")
    print("|---|---|---|---|---|")
    for key, rec in out["runs"].items():
        for sp, m in rec["by_split"].items():
            acc = f"{m['answer_correct']}/{m['answer_n']}" if m["answer_n"] else "-"
            len_ = f"{m['answer_correct_lenient']}/{m['answer_n']}" if m["answer_n"] else "-"
            ref = f"{m['refuse_correct']}/{m['refuse_n']}" if m["refuse_n"] else "-"
            print(f"| {key} | {sp} | {acc} | {len_} | {ref} |")
    print()
    print(json.dumps(out["guard_ablation"], indent=2))
    print("gold verification:", out["gold_verification"])
    for key, rec in out["runs"].items():
        print(f"{key}: shown-vs-scored mismatches {rec['shown_vs_scored_mismatches']} "
              f"over {rec['n_items_shown_and_scored']} items")


if __name__ == "__main__":
    main()
