PY := .venv/bin/python

.PHONY: setup keys dev test lint eval-g eval-e report demo-reset smoke

setup:
	uv venv --python 3.11 .venv
	uv pip install --python $(PY) -e ".[dev]"

keys:
	$(PY) -m approver.keys keys

# Shop (8001), approver (8002) and gateway (8000). Each process gets only the secrets it may hold:
# the approver never sees SHOP_API_KEY and the gateway never sees the approver key (invariant 7).
# The UI (5173) joins on Nov 3.
dev:
	@set -a; [ -f .env ] && . ./.env; set +a; trap 'kill 0' INT TERM EXIT; \
	env -u MODEL_API_KEY -u APPROVER_KEY_PATH -u HF_TOKEN \
	  $(PY) -m uvicorn shop.app:app --port 8001 & \
	env -u SHOP_API_KEY -u MODEL_API_KEY -u WEAK_MODEL_API_KEY -u HF_TOKEN \
	  $(PY) -m uvicorn --factory approver.app:build_from_env --port 8002 & \
	env -u APPROVER_KEY_PATH \
	  $(PY) -m uvicorn --factory intentlock.api:build_from_env --port 8000 & \
	wait

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

# Empty the shop and start a fresh gateway session with the demo mandate (services must be up).
demo-reset:
	@set -a; [ -f .env ] && . ./.env; set +a; $(PY) -m intentlock.cli reset

# One muse-spark structured-output call. Sources .env in this shell only and passes just the MODEL_*
# variables on, so SHOP_API_KEY never reaches the process. The file is never printed.
smoke:
	@[ -f .env ] && . ./.env; \
	MODEL_API_KEY="$$MODEL_API_KEY" MODEL_BASE_URL="$$MODEL_BASE_URL" MODEL_ID="$$MODEL_ID" \
	MODEL_ID_EVAL="$$MODEL_ID_EVAL" LLM_CACHE_DIR="$$LLM_CACHE_DIR" $(PY) -m intentlock.llm.smoke
