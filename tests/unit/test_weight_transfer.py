"""Unit test: weight transfer caps influences at 4 per vertex (mobile ceiling).

Run with:

    blender -b --python-exit-code 1 -P tests/unit/test_weight_transfer.py
"""

from __future__ import annotations

import os
import sys

import bmesh
import bpy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../blender"))

from bpy_scripts.stages.weights import transfer_weights

MAX_WEIGHTS = 4  # CHANGELOG v1.3.0 mobile ceiling


def _make_mesh_with_vg(name: str) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name + "Mesh")
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh)
    bm.free()
    vg = obj.vertex_groups.new(name="Def")
    vg.add(list(range(len(mesh.vertices))), 1.0, "REPLACE")
    return obj


def test_weight_limit():
    body = _make_mesh_with_vg("BASE_BODY")
    shell = _make_mesh_with_vg("CLOTH_Shell")

    result = transfer_weights(shell, body, weight_limit=MAX_WEIGHTS)
    assert result["status"] == "pass", result
    assert result["max_weights_per_vertex"] <= MAX_WEIGHTS
    assert "UN_SHRINK" in result.get("un_shrink_group", "")
