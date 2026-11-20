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

**Status: pending, no rerun results yet.** The run was attempted on 2026-11-20 at commit `6b3cd2b`. The first API call, dev item 1, was refused with HTTP 429 `insufficient_quota`, because the account had no API credit.

- No completion was returned and no tokens were billed.
- No `eval_openai_*` result file or cache entry was written.
- The published numbers above remain the only LLM results in this repository. They come from the Azure deployment.

To run the rerun once credit is available:

```bash
# Make sure results/llm_cache.jsonl does not exist (the published cache is kept as
# results/llm_cache.published-azure-2026-11-19.jsonl, gitignored).
.venv/bin/python -m cohort.evaluate --backend openai --split dev    --tag prompt_v1
.venv/bin/python -m cohort.evaluate --backend openai --split test   --tag frozen_v1
.venv/bin/python -m cohort.evaluate --backend openai --split stress --tag frozen_v1
```
