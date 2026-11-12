"""Pytest bootstrap: make ``bpy_scripts`` importable without Blender.

Blender is not required for the plain-pytest tests: every ``bpy_scripts`` module
guards its ``import bpy`` so it can be imported outside Blender. What those
tests *do* cover is the import wiring, which is exactly what broke CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BLENDER_DIR = REPO_ROOT / "blender"

# Same bootstrap as blender/run_pipeline.py: ``blender/`` on sys.path, and only
# absolute ``bpy_scripts.*`` imports. Adding REPO_ROOT as well would make the
# same module importable as ``blender.bpy_scripts.*`` too, and mixing the two
# is what raises "attempted relative import beyond top-level package".
if str(BLENDER_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_DIR))

try:  # the bpy test suites can only run inside real Blender
    import bmesh  # noqa: F401
    import bpy  # noqa: F401

    HAS_BPY = True
except Exception:  # any import failure means "not inside Blender"
    HAS_BPY = False

# Skips only the bpy-dependent suites; everything else still runs anywhere.
collect_ignore_glob: list[str] = []
if not HAS_BPY:
    collect_ignore_glob += [
        "integration/*.py",
        "unit/test_shrinkwrap_offset.py",
        "unit/test_weight_transfer.py",
    ]
