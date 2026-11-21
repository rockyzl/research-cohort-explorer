# Reproduction addendum, 2026-11-20: frozen evaluation rerun on the OpenAI API

## Why

The published LLM results came from an Azure OpenAI GPT-4.1 deployment, model version `gpt-4.1-2025-04-14`, configured through environment variables. They are:

- dev 12/12 answers and 3/3 refusals;
- test 28/28 and 6/6;
- stress 14/16 strict, 15/16 lenient, 1/2 refusals.

These results are in `results/eval_azure-openai_*.json`. This addendum records a rerun of the same frozen evaluation on the public OpenAI API, against the same model snapshot. Anyone with an OpenAI API key can then reproduce the numbers without access to a particular Azure resource.

## Pre-registration (written and committed before the rerun)

**What changes: the transport only.**
- `cohort.nl2sql.OpenAILLM` calls the OpenAI API with model `gpt-4.1-2025-04-14`, the same snapshot the Azure deployment returned in every cached response.
- It shares prompt construction, call parameters and parsing with `AzureLLM` through a common base class.
- The call parameters are `temperature=0`, `max_tokens=700` and `response_format=json_object`, with no seed.
- The Azure code path is kept unchanged, including its cache key.

**What does not change:**
- the prompt (v1, frozen at `069b0fd`);
- the catalog-rendered schema;
- the gold and stress sets;
- the guard, the output policy and small-cell suppression;
- the comparator and scoring.

**Verified before the run:**
- For all 67 dev, test and stress items, the first-attempt messages built by `OpenAILLM` were hashed with the original cache-key formula. All 67 hashes are present in the published run's LLM cache, so the prompts are byte-identical to the published run.
- Repair-turn messages depend on the model's first output and cannot be checked in advance.
- `tests/test_llm_providers.py` checks offline that both providers build identical messages for all three prompt variants and send identical call parameters.

**Run protocol:**
- The published LLM cache is moved aside, not deleted, so every call is fresh: `llm_calls_from_cache` must be 0.
- dev, test and stress are each run once with `--backend openai`. The refusal items are part of those splits.
- The ablations (`bare`, `permissive`) are not rerun in this addendum.
- The output files are `results/eval_openai_{dev_prompt_v1,test_frozen_v1,stress_frozen_v1}.json`.
- The published `eval_azure-openai_*.json` files are left untouched.

**Reproduction criterion, fixed in advance:**
- The rerun **reproduces** the published evaluation if, for each of dev, test and stress, it gives the same strict score, lenient score and refusal score.
- Per-item agreement (same outcome and correctness per item, and identical final SQL) is reported separately. Matching aggregates can hide items that flipped in opposite directions.
- Any difference is reported next to the published number, with each run labelled. Neither run replaces the other.

**Known failure cases to check for recurrence:**
- S11, node-positive mapped to a stage proxy;
- S10 and S18, an extra column;
- S15, `PERCENTILE_CONT` followed by a `LIMIT` expression sqlglot cannot parse.

**Budget:** a USD 3 spend cap, with tokens and cost recorded below. The published run used about 146k input and 3.6k output tokens across these three splits.

## Results

**Verdict: reproduced.** The pre-registered criterion is met: dev, test and stress give the same strict, lenient and refusal scores as the published run. Per-item outcome and correctness agree on all 67 items.

**Run details:**
- The first attempt on 2026-11-20, at commit `6b3cd2b`, was refused on its first call with HTTP 429 `insufficient_quota` because the account had no API credit. Nothing was returned or billed.
- After credit was added, the three splits were each run once at the clean commit `5e50545`. The commits between the pre-registration and the run changed only docs and `scripts/summarize.py`, not the call path.
- Run time: `2026-11-21T01:09Z` (UTC).
- The published LLM cache was moved aside first. All 69 calls were fresh (`llm_calls_from_cache` = 0).
- Every response reported model `gpt-4.1-2025-04-14`, with system fingerprint `fp_1a2c6bc737` on 68 calls and `fp_3c6b77ab8c` on 1.
- The result files are `results/eval_openai_{dev_prompt_v1,test_frozen_v1,stress_frozen_v1}.json`, and they are summarised under `openai_v1_*` in `results/summary.json`. The published `eval_azure-openai_*.json` files are unchanged.

| Split | Published run (Azure deployment) | OpenAI API rerun |
|---|---|---|
| dev | 12/12 strict, 12/12 lenient, refusals 3/3 | 12/12 strict, 12/12 lenient, refusals 3/3 |
| test (held out) | 28/28, 28/28, refusals 6/6 | 28/28, 28/28, refusals 6/6 |
| stress | 14/16 strict, 15/16 lenient, refusals 1/2 | 14/16 strict, 15/16 lenient, refusals 1/2 |
| silent wrong answers (stress) | S10 | S10 |
| guard-blocked (stress) | S15 | S15 |
| shown-vs-scored mismatches | 0 | 0 |

### Per-item agreement

| Split | Items | Same outcome | Same correctness (strict and lenient) | Byte-identical final SQL |
|---|---|---|---|---|
| dev | 15 | 15 | 15 | 10 |
| test | 34 | 34 | 34 | 13 |
| stress | 18 | 18 | 18 | 8 |

Temperature 0 does not make the output deterministic across serving stacks. The SQL text differs on 36 of 67 items:
- column aliases and whitespace;
- `COUNT(*)` against `COUNT(DISTINCT patient_id)` on patient-grain tables;
- equivalent formulations, such as D07 summing `data_availability.n_diagnostic_slides` instead of counting `slide_images` rows, both 1127, and T18 using the availability flags instead of `NOT IN`, both 2.

No item changed outcome or correctness.

### Known failure cases

- **S11 "How many deaths among node-positive patients?": recurred.**
  - The model again answered instead of refusing, with the same invented stage proxy for nodal status.
  - It used `ajcc_stage IN ('Stage IIA', 'Stage IIB', 'Stage III', 'Stage IIIA', 'Stage IIIB', 'Stage IIIC', 'Stage IV')`. This time it also included Stage IV and gave no stated reason.
  - The result was 124 deaths, against 109 in the published run.
  - Stage does not determine nodal status (T2N0 is stage IIA), so the query is wrong in both runs. It is valid, aggregate and suppressed, and passes every guard.
  - The finding in `docs/evaluation.md` therefore holds on the second provider: semantic hallucination is caught only by the refusal item, not by the guard.
- **S10 "PAM50 subtype with the highest mean ESR1": same.**
  - The answer is right (LumB) but includes the extra average column, so it fails strict and passes lenient.
  - The SQL is equivalent to the published query.
- **S15 "median age at diagnosis": same outcome, different path.**
  - In the published run, the first attempt used `PERCENTILE_CONT` (blocked by the function allow-list) and the repair used the `LIMIT 2 - (…) % 2 OFFSET …` median.
  - In the rerun, the first attempt went straight to the `LIMIT`-arithmetic median, and both attempts were rejected because sqlglot cannot parse them.
  - Result in both runs: a guard false positive, failing closed.
- **S18 "PFI event rate by triple-negative status": correct under strict in both runs.**
  - The extra-column failure for S18 described in `docs/evaluation.md` comes from the `bare` and `permissive` ablations, not from prompt v1.
- **S17 (an "either" item, row-level request): same.** The `aggregate_only` rejection was repaired to a count of 20, which is acceptable for an "either" item.

### Tokens and cost

- 146,435 input tokens, of which 124,928 were served from OpenAI's automatic prompt cache, plus 3,476 output tokens.
- The published run used 146,381 input and 3,624 output tokens.
- Estimated cost:
  - **about USD 0.20** at USD 3.00, 0.75 and 12.00 per million uncached input, cached input and output tokens (the listed price for this dated snapshot);
  - about USD 0.13 at the standard USD 2.00, 0.50 and 8.00 GPT-4.1 rates;
  - at most USD 0.48 even with no prompt caching.
- The USD 3 cap was not approached.

### Not rerun

The `bare` and `permissive` ablations and `docs/demo-transcript.md` remain from the original provider only.

### Rerun command

```bash
# With results/llm_cache.jsonl absent or moved aside:
.venv/bin/python -m cohort.evaluate --backend openai --split dev    --tag prompt_v1
.venv/bin/python -m cohort.evaluate --backend openai --split test   --tag frozen_v1
.venv/bin/python -m cohort.evaluate --backend openai --split stress --tag frozen_v1
.venv/bin/python scripts/summarize.py
```
