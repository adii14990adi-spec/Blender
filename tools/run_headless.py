#!/usr/bin/env python3
"""Run ``blender/run_pipeline.py`` through the ``bpy`` Python module.

Useful for local development and for CI runners that install ``bpy`` from PyPI
instead of the Blender tarball. The real Blender binary is still the supported
production path:

    blender -b --python-exit-code 1 --python blender/run_pipeline.py -- --variant male

    python tools/run_headless.py --variant male --outfit jacket_urban_01 --stage full
"""

from __future__ import annotations

import os
import runpy
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "blender", "run_pipeline.py")


def main() -> int:
    try:
        import bpy  # noqa: F401
    except ImportError:
        print(
            "bpy is not installed. Install it with `pip install bpy==5.0.0` (Python 3.11) "
            "or use the Blender binary: "
            "blender -b --python-exit-code 1 --python blender/run_pipeline.py",
            file=sys.stderr,
        )
        return 2

    sys.argv = [SCRIPT, *sys.argv[1:]]
    try:
        runpy.run_path(SCRIPT, run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
