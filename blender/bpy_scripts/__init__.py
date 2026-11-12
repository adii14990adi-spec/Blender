"""Headless bpy pipeline package.

Imported as ``bpy_scripts`` (``blender/`` is placed on ``sys.path`` by
``blender/run_pipeline.py``). Keeping this a regular package — rather than an
implicit namespace package — makes module names stable and keeps relative
imports resolving against a single import path.
"""

from __future__ import annotations

__all__ = ["pipeline"]
