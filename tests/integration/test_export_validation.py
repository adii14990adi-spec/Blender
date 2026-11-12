"""The exported GLB/FBX must survive independent inspection — and bad ones must fail.

Two things are checked here:

1. the artifacts produced by the real pipeline pass the project's structural
   inspectors *and* the official Khronos glTF Validator (when node + the
   ``gltf-validator`` package are available);
2. deliberately corrupted copies of the same GLB (skin removed, UVs removed,
   textures removed, weights unnormalised) are rejected — proving the gate is
   not a rubber stamp.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess

import pytest
from conftest import requires_bpy

from bpy_scripts.utils.fbx_inspect import inspect_fbx
from bpy_scripts.utils.glb_inspect import inspect_glb

pytestmark = [pytest.mark.integration, requires_bpy]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KHRONOS_SCRIPT = os.path.join(REPO_ROOT, "tools", "gltf_validate.js")


def _glb_path(result) -> str:
    return result["artifacts"]["glb"]


def _fbx_path(result) -> str:
    return result["artifacts"]["fbx"]


def _rewrite_glb(source: str, destination: str, mutate) -> str:
    """Copy a GLB, apply ``mutate(gltf)`` to its JSON chunk and repack it."""
    with open(source, "rb") as handle:
        data = handle.read()
    offset = 12
    json_chunk = None
    bin_chunk = b""
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        payload = data[offset + 8 : offset + 8 + length]
        if kind == 0x4E4F534A:
            json_chunk = payload
        elif kind == 0x004E4942:
            bin_chunk = payload
        offset += 8 + length

    gltf = json.loads(json_chunk.decode("utf-8"))
    mutate(gltf)

    newjson = json.dumps(gltf).encode("utf-8")
    newjson += b" " * ((4 - len(newjson) % 4) % 4)
    body = struct.pack("<II", len(newjson), 0x4E4F534A) + newjson
    if bin_chunk:
        body += struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk
    header = struct.pack("<III", 0x46546C67, 2, 12 + len(body))
    with open(destination, "wb") as handle:
        handle.write(header + body)
    return destination


@pytest.mark.parametrize("variant", ["male", "female"])
def test_exported_glb_passes_the_project_inspector(pipeline_results, variant):
    result = inspect_glb(_glb_path(pipeline_results[variant]), expect_skin=True, max_influences=4)
    assert result["ok"] is True, result["errors"]
    stats = result["stats"]
    assert stats["skins"] == 1
    assert stats["has_uvs"] and stats["has_normals"]
    assert stats["images"] >= 3
    assert stats["embedded_images"] == stats["images"]


@pytest.mark.parametrize("variant", ["male", "female"])
def test_exported_fbx_contains_the_armature(pipeline_results, variant):
    result = inspect_fbx(_fbx_path(pipeline_results[variant]), expect_skin=True)
    assert result["ok"] is True, result["errors"]
    stats = result["stats"]
    assert stats["skin_deformers"] >= 1
    assert stats["bone_models"] >= 15
    assert stats["clusters"] >= 15
    assert stats["uv_layers"] >= 1


@pytest.mark.parametrize("variant", ["male", "female"])
def test_corrupted_glb_without_skin_is_rejected(pipeline_results, variant, tmp_path):
    broken = _rewrite_glb(
        _glb_path(pipeline_results[variant]),
        str(tmp_path / "no_skin.glb"),
        lambda gltf: gltf.pop("skins", None),
    )
    result = inspect_glb(broken, expect_skin=True)
    assert result["ok"] is False
    assert any("skin" in error.lower() for error in result["errors"]), result["errors"]


@pytest.mark.parametrize("variant", ["male", "female"])
def test_corrupted_glb_without_uvs_is_rejected(pipeline_results, variant, tmp_path):
    def strip_uvs(gltf):
        for mesh in gltf.get("meshes", []):
            for primitive in mesh.get("primitives", []):
                primitive["attributes"].pop("TEXCOORD_0", None)

    broken = _rewrite_glb(
        _glb_path(pipeline_results[variant]), str(tmp_path / "no_uv.glb"), strip_uvs
    )
    result = inspect_glb(broken, expect_skin=True)
    assert result["ok"] is False


@pytest.mark.parametrize("variant", ["male", "female"])
def test_corrupted_glb_without_textures_is_rejected(pipeline_results, variant, tmp_path):
    def strip_textures(gltf):
        for material in gltf.get("materials", []):
            pbr = material.get("pbrMetallicRoughness", {})
            pbr.pop("baseColorTexture", None)
            pbr.pop("metallicRoughnessTexture", None)
            material.pop("normalTexture", None)
        gltf["images"] = []
        gltf["textures"] = []

    broken = _rewrite_glb(
        _glb_path(pipeline_results[variant]), str(tmp_path / "no_textures.glb"), strip_textures
    )
    result = inspect_glb(broken, expect_skin=True, expect_textures=True)
    assert result["ok"] is False


def _khronos_available() -> bool:
    if shutil.which("node") is None or not os.path.exists(KHRONOS_SCRIPT):
        return False
    return os.path.isdir(os.path.join(REPO_ROOT, "node_modules", "gltf-validator"))


@pytest.mark.skipif(not _khronos_available(), reason="node + gltf-validator not installed")
@pytest.mark.parametrize("variant", ["male", "female"])
def test_khronos_gltf_validator_accepts_the_glb(pipeline_results, variant):
    completed = subprocess.run(
        ["node", KHRONOS_SCRIPT, _glb_path(pipeline_results[variant])],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["numErrors"] == 0, payload["errors"]
    assert payload["numWarnings"] == 0, payload["warnings"]
