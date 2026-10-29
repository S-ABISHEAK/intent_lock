PY := .venv/bin/python

.PHONY: setup dev test lint eval-g eval-e report demo-reset smoke

setup:
	uv venv --python 3.11 .venv
	uv pip install --python $(PY) -e ".[dev]"

dev:
	@echo "not implemented yet: see SPEC §3 (services) and §16"; exit 1

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

eval-g:
	@echo "not implemented yet: see SPEC §13.1"; exit 1

eval-e:
	@echo "not implemented yet: see SPEC §13.2"; exit 1

report:
	@echo "not implemented yet: see SPEC §13.3"; exit 1

demo-reset:
	@echo "not implemented yet: see SPEC §18"; exit 1

# One muse-spark structured-output call. Sources .env in this shell only and passes just the MODEL_*
# variables on, so SHOP_API_KEY never reaches the process. The file is never printed.
smoke:
	@[ -f .env ] && . ./.env; \
	MODEL_API_KEY="$$MODEL_API_KEY" MODEL_BASE_URL="$$MODEL_BASE_URL" MODEL_ID="$$MODEL_ID" \
	MODEL_ID_EVAL="$$MODEL_ID_EVAL" LLM_CACHE_DIR="$$LLM_CACHE_DIR" $(PY) -m intentlock.llm.smoke
