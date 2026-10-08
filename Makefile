# IaC/CD Understanding Benchmark
# Usage: make run MODEL=claude-sonnet-4-5-20250929

SHELL := bash
PYTHON := .venv/bin/python3
RESULTS := results

.PHONY: deps test run report run-e2e run-cold compare preflight validate

deps:
	($(PYTHON) -m pip install -e . -e ".[dev]")

test:
	($(PYTHON) -m pytest tests/ -v)

# Tooling health: does this machine have every binary the stacks' stages
# invoke? The runner does this itself before spending a token; this target is
# for checking a machine before committing to a matrix.
# make preflight STACKS=knr-ops,chant
preflight:
	($(PYTHON) -m bench.preflight --stacks $(or $(STACKS),all) $(if $(E2E),--e2e))

# Classify every run in a result set valid/partial/rejected before quoting it.
# make validate DIRS="results/claude-opus-5" VERBOSE=1
validate:
	($(PYTHON) -m bench.validate $(or $(DIRS),$(RESULTS)/*) $(if $(VERBOSE),--verbose))

# Run benchmark for a single model
# Make run MODEL=claude-sonnet-4-5-20250929 STACK=knr-ops
# Make run MODEL=claude-sonnet-4-5-20250929 STACK=all TASK=T2-generate
# Make run MODEL=claude-sonnet-4-5-20250929 PROVIDER=openai-compat BASE_URL=http://localhost:8000
# Make run MODEL=claude-opus-5 JUDGE=1   (idiom axis via rubric judge; costs extra API calls)
run:
	($(PYTHON) -m bench.runner \
		--model $(MODEL) \
		--model-provider $(or $(PROVIDER),anthropic) \
		$(if $(BASE_URL),--model-args --base-url $(BASE_URL)) \
		$(if $(STACK),--stack $(STACK)) \
		$(if $(TASK),--task $(TASK)) \
		$(if $(TASKS),--tasks $(TASKS)) \
		-k $(or $(K),3) \
		--condition $(or $(CONDITION),warm) \
		$(if $(JUDGE),--judge) \
		$(if $(JUDGE_MODEL),--judge-model $(JUDGE_MODEL)) \
		$(if $(API_KEY),--api-key $(API_KEY)))

# Run with e2e validation (requires kind + docker)
run-e2e:
	($(PYTHON) -m bench.runner \
		--model $(MODEL) \
		--model-provider $(or $(PROVIDER),anthropic) \
		$(if $(BASE_URL),--model-args --base-url $(BASE_URL)) \
		$(if $(STACK),--stack $(STACK)) \
		$(if $(TASK),--task $(TASK)) \
		-k $(or $(K),3) \
		--condition $(or $(CONDITION),warm) \
		--e2e \
		$(if $(API_KEY),--api-key $(API_KEY)))

# Generate markdown report from results
# make report MODEL=claude-opus-5
report:
	($(PYTHON) -m bench.report --model $(MODEL) $(if $(OUTPUT),--output $(OUTPUT)))

# Generate comparative report across result sets (default: every dir in results/)
# make compare DIRS="results/claude-opus-5 results/gpt-5.4"
compare:
	($(PYTHON) -m bench.report --compare $(or $(DIRS),$(RESULTS)/*) $(if $(OUTPUT),--output $(OUTPUT)))

# Run cold condition (no docs injected)
run-cold:
	($(PYTHON) -m bench.runner \
		--model $(MODEL) \
		--model-provider $(or $(PROVIDER),anthropic) \
		$(if $(BASE_URL),--model-args --base-url $(BASE_URL)) \
		$(if $(STACK),--stack $(STACK)) \
		-k $(or $(K),3) \
		--condition cold \
		$(if $(API_KEY),--api-key $(API_KEY)))
