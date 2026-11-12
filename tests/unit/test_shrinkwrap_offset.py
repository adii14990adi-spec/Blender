"""Unit test: shrinkwrap keeps the shell outside the body (no poke-through).

Run with:

    blender -b --python-exit-code 1 -P tests/unit/test_shrinkwrap_offset.py
"""

from __future__ import annotations

import os
import sys

import bmesh
import bpy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../blender"))

from bpy_scripts.stages.shrinkwrap import shrinkwrap_to_body


def _make_cube(name: str, size: float = 1.0, z: float = 0.0) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name + "Mesh")
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(mesh)
    bm.free()
    obj.location.z = z
    return obj


def test_no_intersection():
    # Body cube centered at z=0 (spans -0.5..0.5); shell cube above at z=2.
    # Shrinkwrap projects the shell down onto the body's top face + offset.
    body = _make_cube("BASE_BODY", size=1.0, z=0.0)
    shell = _make_cube("CLOTH_Shell", size=1.0, z=2.0)

    result = shrinkwrap_to_body(shell, body, offset=0.004)
    assert result["status"] == "pass", result
    assert result["shell_vertex_count"] > 0

    shell_min_z = min(v.co.z for v in shell.data.vertices)
    body_min_z = min(v.co.z for v in body.data.vertices)
    assert shell_min_z > body_min_z + 0.001, (
        f"shell min_z {shell_min_z} not outside body min_z {body_min_z}"
    )
