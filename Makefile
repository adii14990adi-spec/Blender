# Modular Clothing Pipeline - local dev targets.
#
# Blender 5.0 is required. Either put the Blender binary on PATH as `blender`,
# or install the module (`pip install bpy==5.0.0`, Python 3.11) and use
# `make BLEND="python tools/run_headless.py --"` style invocation

BLEND    ?= blender
VARIANT  ?= male
VARIANTS ?= male female
OUTFIT   ?= jacket_urban_01
OUT_DIR  := outputs

# `blender -b --python-exit-code 1 --python blender/run_pipeline.py -- <args>`
PIPELINE := $(BLEND) -b --python-exit-code 1 --python blender/run_pipeline.py --

.PHONY: help assets build build-all validate test test-unit test-integration report clean

help:
	@echo "make assets            - list body variants and outfits"
	@echo "make build             - full pipeline for VARIANT=$(VARIANT)"
	@echo "make build-all         - full pipeline for every variant ($(VARIANTS))"
	@echo "make validate          - run the validators on the newest checkpoint"
	@echo "make test              - unit + integration tests (needs bpy)"
	@echo "make report            - regenerate docs/BUILD_REPORT.md from the run JSONs"
	@echo "make clean             - remove generated outputs"

assets:
	$(PIPELINE) --list-assets

build:
	$(PIPELINE) --stage full --variant $(VARIANT) --outfit $(OUTFIT) --out /tmp/build_$(VARIANT).json

build-all:
	@for variant in $(VARIANTS); do \
		echo "=== $$variant ==="; \
		$(PIPELINE) --stage full --variant $$variant --outfit $(OUTFIT) --out /tmp/build_$$variant.json || exit 1; \
	done

validate:
	$(PIPELINE) --stage validate --variant $(VARIANT) --outfit $(OUTFIT) --out /tmp/validate_$(VARIANT).json

test: test-unit test-integration

test-unit:
	python -m pytest tests/unit -m unit -q

test-integration:
	python -m pytest tests/integration -m integration -q

report:
	python tools/build_report.py \
		--run male=/tmp/build_male.json \
		--run female=/tmp/build_female.json \
		--out docs/BUILD_REPORT.md \
		--json outputs/build_report.json

clean:
	rm -rf $(OUT_DIR)/*
