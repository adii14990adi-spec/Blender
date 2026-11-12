"""Regression tests for the import wiring that broke the Blender CI build.

The original failure was::

    ImportError: attempted relative import beyond top-level package

raised from ``blender/bpy_scripts/stages/*.py``, which used ``from ...utils``
(three dots) inside a two-level-deep module. Blender runs
``blender/run_pipeline.py`` as a top-level script, so ``bpy_scripts`` is the
top-level package and level 3 has nowhere to go.

These tests run under plain pytest — no Blender needed.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import bpy_scripts

REPO_ROOT = Path(__file__).resolve().parents[2]
BLENDER_DIR = REPO_ROOT / "blender"
PACKAGE_DIR = BLENDER_DIR / "bpy_scripts"


def _all_modules() -> list[str]:
    names = ["bpy_scripts"]
    names.extend(
        info.name for info in pkgutil.walk_packages(bpy_scripts.__path__, prefix="bpy_scripts.")
    )
    return sorted(names)


@pytest.mark.unit
def test_every_pipeline_module_imports_outside_blender() -> None:
    """Every module must import with ``bpy_scripts`` as the top-level package."""
    for name in _all_modules():
        importlib.import_module(name)


@pytest.mark.unit
def test_no_relative_import_escapes_the_package() -> None:
    """No module may use more ``.`` than its package depth allows."""
    problems = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        relative = path.relative_to(PACKAGE_DIR)
        package_parts = relative.parts[:-1]  # ("stages",) for stages/shell.py
        max_level = 1 + len(package_parts)

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level > max_level:
                problems.append(
                    f"{relative.as_posix()}:{node.lineno} uses level {node.level} "
                    f"(max {max_level}) — use absolute 'bpy_scripts....' imports"
                )

    assert not problems, "\n".join(problems)


@pytest.mark.unit
def test_run_pipeline_bootstraps_bpy_scripts() -> None:
    """``blender/run_pipeline.py`` puts ``blender/`` on sys.path itself."""
    import run_pipeline  # imported for its side effects (sys.path bootstrap)

    assert run_pipeline.BLENDER_DIR == BLENDER_DIR
    assert str(BLENDER_DIR) in __import__("sys").path
