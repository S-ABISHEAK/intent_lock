PY := .venv/bin/python

.PHONY: weak-smoke two-channel-check setup keys doctor promptguard-check dev session mandate approve agent timeline test lint eval-g eval-e report demo-reset smoke

setup:
	uv venv --python 3.11 .venv
	uv pip install --python $(PY) -e ".[dev]"

keys:
	$(PY) -m approver.keys keys

# Which required .env names are set (never prints values) and whether the services are up.
REQUIRED = MODEL_API_KEY SHOP_API_KEY GATEWAY_OPERATOR_TOKEN APPROVER_UI_TOKEN CONFIG
doctor:
	@set -a; [ -f .env ] && . ./.env; set +a; \
	[ -f .env ] || echo ".env: MISSING (copy .env.example to .env)"; \
	for v in $(REQUIRED); do eval "val=\$$$$v"; [ -n "$$val" ] && echo "$$v: set" || echo "$$v: MISSING"; done; \
	[ -f "$${APPROVER_PUBLIC_KEY_PATH:-keys/approver.pub}" ] && echo "approver keys: present" || echo "approver keys: MISSING (run make keys)"; \
	for u in "$${SHOP_URL:-http://localhost:8001}" "$${APPROVER_URL:-http://localhost:8002}" "$${GATEWAY_URL:-http://localhost:8000}"; do \
	  printf "%s/health: " "$$u"; curl -s -m 2 "$$u/health" || printf "not running"; echo; done

# Load Prompt Guard 2 like the gateway does and score a benign and an injected sample.
promptguard-check:
	@set -a; [ -f .env ] && . ./.env; set +a; \
	env -i PATH="$$PATH" HOME="$$HOME" HF_TOKEN="$$HF_TOKEN" $(PY) -m intentlock.verify.pg_check

# SPEC §13.2 weak baseline agent: one tool-calling turn (key never printed).
weak-smoke:
	@set -a; [ -f .env ] && . ./.env; set +a; \
	env -i PATH="$$PATH" HOME="$$HOME" WEAK_MODEL_BASE_URL="$$WEAK_MODEL_BASE_URL" \
	WEAK_MODEL_API_KEY="$$WEAK_MODEL_API_KEY" WEAK_MODEL_ID="$$WEAK_MODEL_ID" $(PY) -m intentlock.llm.weak_smoke

# SPEC §11 live check: Playwright screenshots of the clean/P3/P4 pages, read by Muse.
two-channel-check:
	@set -a; [ -f .env ] && . ./.env; set +a; \
	env -i PATH="$$PATH" HOME="$$HOME" MODEL_API_KEY="$$MODEL_API_KEY" MODEL_BASE_URL="$$MODEL_BASE_URL" \
	MODEL_ID="$$MODEL_ID" LLM_CACHE_DIR="$$LLM_CACHE_DIR" $(PY) -m intentlock.verify.tc_check

# Shop (8001), approver (8002) and gateway (8000). Each process starts from an empty environment
# and gets an explicit allow-list: the approver never sees SHOP_API_KEY, the gateway never sees the
# approver key path or the UI token (invariant 7, D-026). The UI (5173) joins on Nov 3.
dev:
	@set -a; [ -f .env ] && . ./.env; set +a; trap 'kill 0' INT TERM EXIT; \
	env -i PATH="$$PATH" HOME="$$HOME" SHOP_API_KEY="$$SHOP_API_KEY" \
	  $(PY) -m uvicorn shop.app:app --port 8001 & \
	env -i PATH="$$PATH" HOME="$$HOME" APPROVER_UI_TOKEN="$$APPROVER_UI_TOKEN" \
	  APPROVER_KEY_PATH="$${APPROVER_KEY_PATH:-keys/approver.key}" \
	  $(PY) -m uvicorn --factory approver.app:build_from_env --port 8002 & \
	env -i PATH="$$PATH" HOME="$$HOME" SHOP_API_KEY="$$SHOP_API_KEY" SHOP_URL="$$SHOP_URL" \
	  CONFIG="$$CONFIG" GATEWAY_OPERATOR_TOKEN="$$GATEWAY_OPERATOR_TOKEN" \
	  APPROVER_PUBLIC_KEY_PATH="$$APPROVER_PUBLIC_KEY_PATH" GATEWAY_DB_DIR="$$GATEWAY_DB_DIR" \
	  TWO_CHANNEL="$$TWO_CHANNEL" PROMPT_GUARD="$$PROMPT_GUARD" MODEL_API_KEY="$$MODEL_API_KEY" \
	  MODEL_BASE_URL="$$MODEL_BASE_URL" MODEL_ID="$$MODEL_ID" LLM_CACHE_DIR="$$LLM_CACHE_DIR" \
	  HF_TOKEN="$$HF_TOKEN" \
	  $(PY) -m uvicorn --factory intentlock.api:build_from_env --port 8000 & \
	wait

# Operator commands: always the project venv, each with only the variables it needs.
CLI_ENV = env -i PATH="$$PATH" HOME="$$HOME" GATEWAY_URL="$$GATEWAY_URL" APPROVER_URL="$$APPROVER_URL" \
	GATEWAY_OPERATOR_TOKEN="$$GATEWAY_OPERATOR_TOKEN" APPROVER_UI_TOKEN="$$APPROVER_UI_TOKEN"

session:
	@set -a; [ -f .env ] && . ./.env; set +a; $(CLI_ENV) $(PY) -m intentlock.cli session

# Muse drafts a mandate from your words; you read it back and confirm (SPEC §9).
mandate:
	@set -a; [ -f .env ] && . ./.env; set +a; $(CLI_ENV) $(PY) -m intentlock.cli mandate "$(TEXT)"

# The user's side of an approval: shows each card and asks y/N.
approve:
	@set -a; [ -f .env ] && . ./.env; set +a; $(CLI_ENV) $(PY) -m intentlock.cli approve --follow

# The receipts of the current gateway run, one line each, plus the chain check.
timeline:
	@set -a; [ -f .env ] && . ./.env; set +a; $(CLI_ENV) $(PY) -m intentlock.cli timeline

# The agent gets the gateway URL and model settings only, never shop or operator secrets.
TASK ?= Buy a wireless mouse under ₹2,000
agent:
	@set -a; [ -f .env ] && . ./.env; set +a; \
	env -i PATH="$$PATH" HOME="$$HOME" GATEWAY_URL="$$GATEWAY_URL" MODEL_API_KEY="$$MODEL_API_KEY" \
	  MODEL_BASE_URL="$$MODEL_BASE_URL" MODEL_ID="$$MODEL_ID" MODEL_ID_EVAL="$$MODEL_ID_EVAL" \
	  LLM_CACHE_DIR="$$LLM_CACHE_DIR" WEAK_MODEL_BASE_URL="$$WEAK_MODEL_BASE_URL" \
	  WEAK_MODEL_API_KEY="$$WEAK_MODEL_API_KEY" WEAK_MODEL_ID="$$WEAK_MODEL_ID" \
	  $(PY) -m agent.run --task "$(TASK)"

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

eval-g:
	$(PY) -m eval.run_g

eval-e:
	@echo "not implemented yet: see SPEC §13.2"; exit 1

report:
	$(PY) -m eval.report

# Empty the shop and start a fresh gateway session with the demo mandate (services must be up).
# `make demo-reset ATTACKS=P1,P6` loads SPEC §14.3 attack fixtures for a live attack demo.
ATTACKS ?=
demo-reset:
	@set -a; [ -f .env ] && . ./.env; set +a; \
	$(CLI_ENV) SHOP_URL="$$SHOP_URL" SHOP_API_KEY="$$SHOP_API_KEY" \
	  $(PY) -m intentlock.cli reset --attacks "$(ATTACKS)"

# One muse-spark structured-output call. Sources .env in this shell only and passes just the MODEL_*
# variables on, so SHOP_API_KEY never reaches the process. The file is never printed.
smoke:
	@[ -f .env ] && . ./.env; \
	MODEL_API_KEY="$$MODEL_API_KEY" MODEL_BASE_URL="$$MODEL_BASE_URL" MODEL_ID="$$MODEL_ID" \
	MODEL_ID_EVAL="$$MODEL_ID_EVAL" LLM_CACHE_DIR="$$LLM_CACHE_DIR" $(PY) -m intentlock.llm.smoke
