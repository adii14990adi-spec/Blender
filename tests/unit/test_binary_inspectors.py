"""The GLB/FBX inspectors must reject broken files instead of trusting size."""

from __future__ import annotations

import json
import struct

import pytest

from bpy_scripts.utils.fbx_inspect import inspect_fbx, parse_fbx
from bpy_scripts.utils.glb_inspect import GlbError, inspect_glb, parse_glb


def _glb(json_payload: dict, binary: bytes = b"\x00" * 16) -> bytes:
    json_bytes = json.dumps(json_payload).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    bin_bytes = binary + b"\x00" * ((4 - len(binary) % 4) % 4)
    body = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    if bin_bytes:
        body += struct.pack("<II", len(bin_bytes), 0x004E4942) + bin_bytes
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body


@pytest.mark.unit
def test_glb_parser_reads_the_json_chunk(tmp_path):
    path = tmp_path / "ok.glb"
    path.write_bytes(_glb({"asset": {"version": "2.0"}}))
    parsed = parse_glb(str(path))
    assert parsed["json"]["asset"]["version"] == "2.0"
    assert isinstance(parsed["bin"], bytes)
    assert parsed["size"] == path.stat().st_size


@pytest.mark.unit
def test_glb_parser_rejects_a_non_glb_file(tmp_path):
    path = tmp_path / "bad.glb"
    path.write_bytes(b"not a glb at all")
    with pytest.raises(GlbError):
        parse_glb(str(path))


@pytest.mark.unit
def test_inspect_glb_reports_a_missing_skin(tmp_path):
    """A mesh without a skin is exactly the incomplete export we must reject."""
    path = tmp_path / "unskinned.glb"
    binary = struct.pack("<9f", 0, 0, 0, 0, 0, 1, 1, 0, 0)
    path.write_bytes(
        _glb(
            {
                "asset": {"version": "2.0"},
                "scene": 0,
                "scenes": [{"nodes": [0]}],
                "nodes": [{"mesh": 0, "name": "CLOTH_Shell"}],
                "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
                "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
                "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}],
                "buffers": [{"byteLength": 36}],
                "materials": [{"name": "MAT"}],
            },
            binary,
        )
    )

    result = inspect_glb(str(path), expect_skin=True)
    assert result["ok"] is False
    assert any("skin" in error.lower() for error in result["errors"]), result["errors"]


@pytest.mark.unit
def test_inspect_glb_flags_missing_uvs_and_normals(tmp_path):
    path = tmp_path / "no_uv_no_normal.glb"
    binary = struct.pack("<9f", 0, 0, 0, 0, 0, 1, 1, 0, 0)
    path.write_bytes(
        _glb(
            {
                "asset": {"version": "2.0"},
                "scene": 0,
                "scenes": [{"nodes": [0]}],
                "nodes": [{"mesh": 0}],
                "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
                "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
                "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}],
                "buffers": [{"byteLength": 36}],
                "materials": [{"name": "MAT"}],
            },
            binary,
        )
    )
    result = inspect_glb(str(path), expect_skin=False, max_influences=4)
    joined = " ".join(result["errors"]).lower()
    assert result["ok"] is False
    assert "normal" in joined
    assert "texcoord" in joined or "uv" in joined


@pytest.mark.unit
def test_fbx_parser_rejects_ascii_and_garbage(tmp_path):
    path = tmp_path / "not_fbx.fbx"
    path.write_bytes(b"this is not an fbx")
    assert parse_fbx(str(path))["binary"] is False
    result = inspect_fbx(str(path), expect_skin=True)
    assert result["ok"] is False


@pytest.mark.unit
def test_inspect_fbx_reports_errors_without_crashing(tmp_path):
    path = tmp_path / "broken.fbx"
    path.write_bytes(b"Kaydara FBX Binary  \x00" + b"\x00" * 64)
    result = inspect_fbx(str(path), expect_skin=True)
    assert result["ok"] is False
    assert result["errors"]
