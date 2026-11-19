# All targets run on CPU in a few seconds except `eval-llm` (network, ~2 min).
PY := .venv/bin/python
ENV := set -a; [ -f .env ] && . ./.env; set +a;

.PHONY: setup data verify-gold test eval-template eval-llm eval-cached summary demo serve all

setup:
	uv sync

data:            ## copy/download raw files (provenance -> data/manifest.json), build data/cohort.sqlite
	$(PY) scripts/fetch.py
	$(PY) -m cohort.ingest

verify-gold:     ## independent pandas re-computation of every reference answer
	$(PY) gold/verify_pandas.py

test:
	$(PY) -m pytest -q

eval-template:   ## deterministic fallback baseline (no network)
	$(PY) -m cohort.evaluate --backend template --split all --tag baseline

eval-llm:        ## Azure OpenAI runs (needs `az login` + .env); cached calls are reused
	$(ENV) $(PY) -m cohort.evaluate --backend azure --split dev --tag prompt_v1
	$(ENV) $(PY) -m cohort.evaluate --backend azure --split test --tag frozen_v1
	$(ENV) $(PY) -m cohort.evaluate --backend azure --split stress --tag frozen_v1
	$(ENV) $(PY) -m cohort.evaluate --backend azure --split everything --variant bare --tag ablation
	$(ENV) $(PY) -m cohort.evaluate --backend azure --split everything --variant permissive --tag ablation

summary:
	$(PY) scripts/summarize.py

demo:
	$(ENV) $(PY) scripts/demo.py

serve:
	$(ENV) .venv/bin/uvicorn cohort.app:app --port 8790

all: data verify-gold test eval-template summary
