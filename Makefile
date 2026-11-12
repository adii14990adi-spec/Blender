# Modular Clothing Pipeline — local dev targets
# Requires headless Blender on PATH (see docker/Dockerfile.blender).

BLEND   ?= blender
BASE    := assets/base_body/BASE_BODY.blend
REGION  ?= UPPER_BODY
OUTFIT  ?= jacket_urban_01
OUT_DIR := outputs

.PHONY: validate build test lint format clean

validate: ## Run the validate stage only (PR gate)
	$(BLEND) -b --python-exit-code 1 --python blender/run_pipeline.py -- \
		--stage validate \
		--blend $(BASE) \
		--region $(REGION) \
		--out /tmp/validation.json

build: ## Run the full pipeline end-to-end and export FBX + GLB
	$(BLEND) -b --python-exit-code 1 --python blender/run_pipeline.py -- \
		--stage all \
		--blend $(BASE) \
		--region $(REGION) \
		--params "{\"outfit\": \"$(OUTFIT)\"}" \
		--out /tmp/build.json

test: ## Run unit tests inside headless Blender
	$(BLEND) -b --python-exit-code 1 -P tests/unit/test_shrinkwrap_offset.py
	$(BLEND) -b --python-exit-code 1 -P tests/unit/test_weight_transfer.py

lint: ## Lint Python sources
	ruff check blender tests agent

format: ## Format Python sources
	ruff format blender tests agent

clean: ## Remove generated outputs
	rm -rf $(OUT_DIR)/*
