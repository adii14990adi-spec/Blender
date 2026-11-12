"""Integration test: full pipeline for jacket_urban_01.

Run with:

    blender -b --python-exit-code 1 -P tests/integration/test_full_pipeline_jacket.py
"""

from __future__ import annotations

import os
import sys
import tempfile

import bmesh
import bpy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../blender"))

from bpy_scripts.pipeline import run_full_pipeline


def test_full_pipeline_jacket():
    # Build a minimal base-body blend in memory.
    mesh = bpy.data.meshes.new("BASE_BODYMesh")
    obj = bpy.data.objects.new("BASE_BODY", mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh)
    bm.free()

    blend = os.path.join(tempfile.gettempdir(), "test_base_body.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)

    result = run_full_pipeline(blend, region="UPPER_BODY", params={"outfit": "jacket_urban_01"})
    assert result["status"] == "pass", result
    assert result["stage"] == "export"
    assert result["metrics"].get("fbx_path")
    assert result["metrics"].get("fbx_file_size_bytes", 0) > 0
