"""Pytest bootstrap and shared fixtures.

Two test layers:

``tests/unit``
    Pure Python. Every ``bpy_scripts`` module guards its ``import bpy``, so the
    orchestration, manifests, checkpointing, path layout and both binary
    exporters (GLB/FBX inspectors) are testable without Blender.

``tests/integration``
    Requires ``bpy`` (real Blender 5.0). These run the *actual* pipeline for
    both body variants and inspect the files it writes — no mocks. They are
    skipped (not silently passed) when ``bpy`` is unavailable.

All writes are redirected to a temporary directory through
``PIPELINE_OUTPUTS_ROOT`` so a test run can never overwrite a real build.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BLENDER_DIR = REPO_ROOT / "blender"

# Same bootstrap as blender/run_pipeline.py: ``blender/`` on sys.path and only
# absolute ``bpy_scripts.*`` imports.
if str(BLENDER_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_DIR))

# Redirect pipeline writes before any bpy_scripts module reads the constant.
TEST_OUTPUTS = Path(tempfile.gettempdir()) / "clothing_pipeline_pytest_outputs"
if os.environ.get("PIPELINE_TEST_KEEP_OUTPUTS") != "1" and TEST_OUTPUTS.exists():
    shutil.rmtree(TEST_OUTPUTS)
TEST_OUTPUTS.mkdir(parents=True, exist_ok=True)
os.environ["PIPELINE_OUTPUTS_ROOT"] = str(TEST_OUTPUTS)

try:  # only true inside Blender / with the bpy wheel installed
    # ``bpy`` has to be imported before ``bmesh``: the wheel registers the
    # submodules on import. ``importlib`` keeps the order independent of any
    # import-sorting rule.
    import importlib

    bpy = importlib.import_module("bpy")
    bmesh = importlib.import_module("bmesh")

    HAS_BPY = True
    BPY_VERSION: tuple[int, int, int] | None = tuple(bpy.app.version)
except Exception:  # any import failure means "no bpy"
    bpy = None
    bmesh = None
    HAS_BPY = False
    BPY_VERSION = None

requires_bpy = pytest.mark.skipif(
    not HAS_BPY,
    reason="requires the bpy module (Blender 5.0); run inside Blender or `pip install bpy==5.0.0`",
)

OUTFIT = "jacket_urban_01"
VARIANTS = ("male", "female")


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: pure-Python test, no Blender needed")
    config.addinivalue_line("markers", "integration: runs the real Blender pipeline")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def outputs_root() -> Path:
    return TEST_OUTPUTS


@pytest.fixture(scope="session")
def pipeline_results():
    """Run the full pipeline once per body variant and return the results."""
    if not HAS_BPY:
        pytest.skip("requires bpy")
    assert BPY_VERSION is not None and BPY_VERSION >= (5, 0, 0), (
        f"the pipeline targets Blender 5.0+, found {BPY_VERSION}"
    )

    from bpy_scripts.pipeline import run_full_pipeline

    results = {}
    for variant in VARIANTS:
        results[variant] = run_full_pipeline(
            "",
            region="UPPER_BODY",
            params={"variant": variant, "outfit": OUTFIT},
        )
    return results
