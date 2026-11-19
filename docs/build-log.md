# Build log

Chronological record kept *while* building (not reconstructed afterwards). Each entry: what was
attempted, what went wrong, how it was caught. `docs/ai-assisted-engineering.md` summarizes this log.

## 2026-11-19

- **Xena redirect stub.** The first ad-hoc `curl -o` of `BRCA_clinicalMatrix` returned HTTP 302 and
  saved a 110-byte HTML "302 Found" page under the data file name, with exit code 0. Caught by
  inspecting the header line (it was `<html>`), not by the command status. Fix: `scripts/fetch.py`
  follows redirects and refuses `text/html` responses.
- **Reused data verified by hash.** The four files copied from the sibling `tcga-multimodal-text-lab`
  are checked against that lab's recorded SHA-256 before use; `fetch.py` stops on mismatch.
- **Patient universe redefined.** The sibling lab's `clinical.csv` (1078 / 1018 patients) applies
  survival-analysis filters (PFI.time > 0, three-modality join) and a dev/test split. Those are
  wrong for a discovery tool, whose job is to show *who has what*. Ingest rebuilds the universe
  from raw CDR rows instead.
- **Catalog value check caught an incomplete enum.** The hand-written `allowed_values` for
  `clinical.ajcc_stage` omitted `Stage III` (2 patients). Ingest validates every loaded column
  against the catalog and stopped with `values outside catalog allowed_values: {'Stage III'}`.
  Fixed in the catalog; the check stays because the NL-to-SQL prompt shows these enums to the model,
  so a missing value would silently steer generated filters.
- **Function allow-list rejected `AND`.** The first cohort-builder query was refused with
  `function 'and' is not allowed`: sqlglot models `AND`/`OR`/comparisons as `Func` subclasses, so
  walking `exp.Func` treated operators as functions. Caught by the first end-to-end smoke run of
  the cohort builder. Fix: skip `Binary`/`Connector`/`Unary`/`Predicate` nodes; the SQLite
  authorizer remains the backstop for real function calls. The same smoke run showed `instr()`
  appears as `str_position` in sqlglot, which was added to the allow-list.
- **Gold verifier bug (not a data disagreement).** The first run of `gold/verify_pandas.py` crashed:
  `p.hist` resolved to the pandas `DataFrame.hist` plotting method rather than the `hist` column.
  Fixed by indexing `p["hist"]`. After the fix all 40 answerable reference answers agreed with the
  independent pandas re-implementation (`results/gold_verification.json`).
- **Suppression leak found in review, before any test existed.** `run_sql` analysed the
  sqlglot-*qualified* tree to find count columns but executed the user's *original* SQL text.
  qualify names an unaliased `COUNT(*)` `_col_1`, SQLite names it `COUNT(*)`, so nothing matched:
  `WITH t AS (SELECT race, COUNT(*) FROM patients GROUP BY race) SELECT * FROM t` returned
  `AMERICAN INDIAN OR ALASKA NATIVE = 1` unsuppressed. Flagged by an independent review pass
  (advisor model) and reproduced before fixing. Fixes:
  1. the executed SQL is now always generated from the qualified tree (so names match), and is
     re-validated;
  2. the hidden cell-size column is added to every leaf of a set operation (UNION of two small
     counts previously had no cell-size check);
  3. a new researcher-tier rule: the outermost SELECT must aggregate, and identifier columns
     (patient_id, sample_id, file_id, report_id) may not be output except inside an aggregate.
     Row-level output now requires role `approved`. This makes small-cell suppression meaningful;
     before, a researcher could read the same values row by row.
  Regression tests: `tests/test_privacy.py`. All 40 gold reference queries return the same raw
  values through the qualified/rewritten path.
- **Guard change after the first test run.** The test split had already been run once with prompt v1
  when (3) above was added. The prompt did not change, so the dev/test runs were repeated from the
  LLM response cache (identical model outputs, no new calls) to make the saved results reflect the
  final guard code.
- **Guard false positive: valid SQLite that sqlglot cannot parse.** Writing the stress-set median
  reference, `LIMIT 2 - (SELECT COUNT(...) FROM patients) % 2 OFFSET (...)` runs in SQLite but
  sqlglot 27 fails to parse it ("Expecting )"). Parenthesizing the LIMIT expression fixes it. The
  guard fails closed, so this is a usability cost, not a safety hole, but it means the guard
  can reject correct model SQL; it is listed under limitations.
- **Stress set + ablations added after 100%.** Prompt v1 scored 12/12 + 3/3 on dev and 28/28 + 6/6 on
  test, but the prompt rules (her2_status, stage_group, COUNT DISTINCT, primary-tumor samples,
  "excluding missing") were written by the same author as the questions. Added, and committed
  before running: `gold/stress.yaml` (colloquial phrasing), a bare-schema prompt ablation, and a
  permissive-generator ablation that exercises the guard.
- **Provenance stamp polluted by result files.** The harness stamped runs with
  `git describe --dirty`, which marked runs dirty merely because an earlier run in the same
  `make` chain had rewritten `results/*.json`. The dirty check now ignores `results/` and `data/`.
- **Stress / ablation findings (LLM errors no guard can catch).** S11 "node-positive" -> model
  invented "node-positive = stage II-III" under all three prompts; TR4 -> under the bare and
  permissive prompts the model asserted site code `BS` *is* Boston Children's Hospital. Both are
  valid, aggregate, suppressed SQL. Details in `docs/evaluation.md`.
