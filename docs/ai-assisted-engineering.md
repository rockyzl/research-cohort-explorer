# How this project was built (AI-assisted engineering)

This project was built to show how AI-generated code, queries and results can be validated: who contributed what, at what scale, with what checks. This file answers that for this repository, plainly. The chronological evidence is in [`build-log.md`](build-log.md), which was written during the build, and in the git history.

## Who did what

| Role | Actor |
|---|---|
| Problem framing and constraints: target use case, data reuse, guardrail list, gold-set size, honesty rules ("do not fabricate LLM results", "every number from saved results") | Lu, through a written brief to his multi-agent setup |
| Implementation: fetch, catalog, ingest, guard, privacy policy, NL-to-SQL, harness, app, tests, docs | AI coding agent (Claude, run as a sub-agent in Claude Code) |
| Independent review during the build | A separate, stronger reviewer model consulted at checkpoints. It found the suppression leak and the authoring-bias problem (below) |
| NL-to-SQL model under test | Azure OpenAI GPT-4.1 deployment, temperature 0 |
| Human verification | **Pending.** Checklist at the end. Until it is done, Lu should describe this work as "directed and validated an AI-built prototype", not "hand-wrote" |

Scale:
- About 2,000 lines of Python across `src/`, `gold/` and `scripts/`, plus about 430 lines of tests and a 140-line HTML UI.
- A 7-table schema with a 51-column catalog.
- 191 tests.
- 67 evaluation items.
- 225 LLM calls in total (a few US dollars at most on pay-as-you-go).

Build time: one working session.

## What was delegated to AI and how each part was checked

| Component | Delegated | Check that does not trust the generator |
|---|---|---|
| Data ingest | All code | Ingest refuses to load if a column or enum differs from the hand-readable catalog. SHA-256 of reused files is checked against the sibling lab's manifest. Row counts are saved and compared with the sibling lab (1078 survival-eligible patients in both). |
| Reference SQL for the gold set | All 56 answers (gold + stress) | A **second implementation** (`gold/verify_pandas.py`) re-parses the *raw* files with pandas, without the DB or the ingest code, and must agree. 56/56 agree. *Caveat:* the same agent wrote both implementations, so a shared misconception (for example, the HER2 combination rule) would pass. That is why semantic choices are written into the catalog for a human to review. |
| The SQL guard | All code | Two independent layers. The tests switch layer 1 off and show layer 2 still blocks 13 attacks, and show `mode=ro` blocks writes even with the authorizer switched off. |
| Small-cell suppression | All code | Regression tests on known small groups (1 American Indian/Alaska Native patient, 12 male patients, the Basal cohort's 139 vs 138). A standing test checks that the shown (rewritten) result equals the scored (raw) result on every unsuppressed value, for all 56 reference queries. `scripts/summarize.py` repeats the check for every saved evaluation item; there are 0 mismatches. |
| The evaluation harness | All code | Comparator negative controls: off-by-one, wrong key, extra row, wrong order, extra column and empty-vs-zero must all score as wrong. The template baseline must score below 100%; it scored 19/40, so the harness can fail things. |
| LLM-generated SQL | The model writes every query | Guard, then execution against the gold set, then the stress set written after the 100% result, then prompt ablations. |

## Concrete mistakes caught, and what caught them

| # | Mistake | Origin | Caught by |
|---|---|---|---|
| 1 | Xena download saved a 110-byte HTML "302 Found" page as the data file; the exit code was 0 | tool use by the AI agent (`curl` without `-L`) | inspecting the file header before use; `fetch.py` now refuses `text/html` |
| 2 | Catalog enum for `ajcc_stage` missed `Stage III` (2 patients) | AI-written catalog | ingest's catalog-vs-data validation |
| 3 | Function allow-list rejected `AND`, because sqlglot models operators as `Func`; `instr` appears as `str_position` | AI-written guard | first end-to-end smoke run of the cohort builder |
| 4 | `p.hist` resolved to pandas' plotting method instead of the column | AI-written verifier | crash on first run |
| 5 | **Small-cell suppression leak.** The policy analysed the *qualified* SQL but executed the *original* text. An unaliased `COUNT(*)` inside a CTE returned n=1 unsuppressed. | AI-written privacy layer | **the independent reviewer model**, then reproduced; fixed by executing the qualified SQL; regression tests added |
| 6 | Design gap: researchers could read row-level public columns, which made suppression meaningless | AI-written design | same review; researcher tier made aggregate-only, identifiers blocked in output |
| 7 | 100% on dev and test, from rules written with the gold questions in view | AI-written prompt and gold set | the reviewer flagged authoring bias. A stress set was pre-registered and two ablations were run; they show the 100% is mostly a plumbing check |
| 8 | Run provenance stamped "-dirty" by result files | AI-written harness | reading the saved JSON; dirty check now ignores `results/` |
| 8b | **Within-row complement leak in the cohort builder.** For the Basal cohort, `patients=139` and `with_survival=138` revealed that 1 patient lacks survival data; other `with_*` columns leaked the same way. Secondary suppression only worked within a column. The "<11" marker was also shown on complementary cells whose real value was large, such as 61. | AI-written privacy layer | **the independent reviewer model**, which read the live smoke-test output; fixed with within-row complementary suppression and a distinct `suppressed` marker; regression tests added |
| 9 | LLM invented "node-positive = stage II–III" (S11) | GPT-4.1 | stress-set refusal item; no guard can catch this |
| 10 | LLM asserted site code `BS` = Boston Children's Hospital (TR4, bare/permissive prompts) | GPT-4.1 | gold refusal item |
| 11 | Repair loop turned "list names and DOBs" into a count (TR1, permissive) | interaction of guard feedback and the LLM | gold refusal item |
| 12 | Guard rejected valid SQLite median SQL that sqlglot cannot parse (S15) | guard false positive | stress item |
| 13 | Template fallback gave 12 silent wrong answers, e.g. "more than one slide" → any slide | AI-written fallback | gold set |

Lessons:
- Items 1–8b are defects in **AI-written engineering code**, and each was caught by a check that does not share the generator's assumptions: a schema contract, an independent implementation, a reviewer model, or negative controls.
- Items 9–13 are defects in **AI-generated queries**. Here the guard is necessary but not sufficient: it stopped all 7 unsafe queries in the permissive ablation, but 0 of the semantic hallucinations. All 7 unsafe queries were stopped by the static layer; the engine layer's independence is demonstrated by the hand-written attack tests, not by the LLM runs.

## Verification checklist for Lu, before claiming this work

1. Run `make data verify-gold test eval-template summary` on your machine and confirm that the numbers match `results/summary.json`.
2. Spot-check at least 10 gold items by hand: read the question, read the reference SQL, and query the DB yourself. Prioritise T09, T11, T18, T27, T28, D09 and S15.
3. Read `catalog/catalog.yaml`'s `her2_status` and `triple_negative` definitions and decide whether you agree. These are the semantic choices most likely to be wrong in both implementations.
4. Try to break the guard with five queries of your own via `make serve`, then the SQL tab, or `POST /api/sql`.
5. Read `src/cohort/sqlguard.py` and `src/cohort/privacy.py` end to end. Be able to explain why layer 2 exists and what cross-query differencing is.
6. Re-run one LLM split without the cache (delete `results/llm_cache.jsonl` or change the deployment) to see run-to-run variation.
