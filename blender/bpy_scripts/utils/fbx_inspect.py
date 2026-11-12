"""Minimal binary FBX parser used to prove an export really contains a rig.

A file that merely *exists* and has a plausible size is not an FBX we can ship.
Unity silently imports a mesh without a skeleton, so the export stage parses the
node tree and asserts:

* the ``Kaydara FBX Binary`` header and a supported version;
* at least one ``Geometry`` mesh with ``LayerElementUV`` and ``LayerElementNormal``;
* a ``Deformer`` with subtype ``Skin`` and one ``Cluster`` per bound bone;
* bone ``Model`` nodes (``LimbNode``/``Bone``) and their ``Connections``;
* material and texture records when textures are expected.

Pure Python — no ``bpy`` required.
"""

from __future__ import annotations

import os
import struct
import zlib
from typing import Any, Dict, List, Tuple

FBX_BINARY_MAGIC = b"Kaydara FBX Binary  \x00"


class Node:
    __slots__ = ("children", "name", "props")

    def __init__(self, name: str, props: List[Any], children: List["Node"]):
        self.name = name
        self.props = props
        self.children = children

    def child(self, name: str) -> "Node | None":
        for node in self.children:
            if node.name == name:
                return node
        return None

    def children_named(self, name: str) -> List["Node"]:
        return [node for node in self.children if node.name == name]

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Node {self.name} props={len(self.props)} children={len(self.children)}>"


def _read_property(data: bytes, offset: int) -> Tuple[Any, int]:
    kind = data[offset : offset + 1]
    offset += 1
    if kind == b"Y":
        return struct.unpack_from("<h", data, offset)[0], offset + 2
    if kind == b"C":
        return data[offset] != 0, offset + 1
    if kind == b"I":
        return struct.unpack_from("<i", data, offset)[0], offset + 4
    if kind == b"F":
        return struct.unpack_from("<f", data, offset)[0], offset + 4
    if kind == b"D":
        return struct.unpack_from("<d", data, offset)[0], offset + 8
    if kind == b"L":
        return struct.unpack_from("<q", data, offset)[0], offset + 8
    if kind == b"S":
        length = struct.unpack_from("<I", data, offset)[0]
        return data[offset + 4 : offset + 4 + length], offset + 4 + length
    if kind == b"R":
        length = struct.unpack_from("<I", data, offset)[0]
        return data[offset + 4 : offset + 4 + length], offset + 4 + length
    arrays = {
        b"f": ("f", 4),
        b"d": ("d", 8),
        b"l": ("q", 8),
        b"i": ("i", 4),
        b"b": ("?", 1),
    }
    if kind in arrays:
        fmt, size = arrays[kind]
        length, encoding, compressed_len = struct.unpack_from("<III", data, offset)
        payload_start = offset + 12
        payload = data[payload_start : payload_start + compressed_len]
        if encoding == 1:
            payload = zlib.decompress(payload)
        values = list(struct.unpack("<" + fmt * length, payload[: length * size]))
        return values, payload_start + compressed_len
    return None, offset + 1


def _read_node(data: bytes, offset: int, wide: bool) -> Tuple["Node | None", int]:
    header_size = 8 + 8 + 8 + 1 if wide else 4 + 4 + 4 + 1
    if offset + header_size > len(data):
        return None, len(data)
    if wide:
        end_offset, num_props, _prop_len = struct.unpack_from("<QQQ", data, offset)
        name_len = data[offset + 24]
        name_start = offset + 25
    else:
        end_offset, num_props, _prop_len = struct.unpack_from("<III", data, offset)
        name_len = data[offset + 12]
        name_start = offset + 13
    if end_offset == 0:
        return None, offset + header_size
    name = data[name_start : name_start + name_len].decode("utf-8", "replace")
    cursor = name_start + name_len
    props: List[Any] = []
    for _ in range(num_props):
        try:
            value, cursor = _read_property(data, cursor)
        except (struct.error, IndexError, zlib.error):
            break
        props.append(value)
    children: List[Node] = []
    while cursor < end_offset:
        child, cursor = _read_node(data, cursor, wide)
        if child is None:
            break
        children.append(child)
    return Node(name, props, children), end_offset


def parse_fbx(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"FBX not found: {path}")
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.startswith(FBX_BINARY_MAGIC):
        return {"binary": False, "version": None, "root": [], "size": len(data)}
    version = struct.unpack_from("<I", data, 23)[0]
    wide = version >= 7500
    offset = 27
    root: List[Node] = []
    while offset < len(data):
        node, next_offset = _read_node(data, offset, wide)
        if node is None:
            break
        root.append(node)
        if next_offset <= offset:
            break
        offset = next_offset
    return {"binary": True, "version": version, "root": root, "size": len(data)}


def _find(root: List[Node], name: str) -> Node | None:
    for node in root:
        if node.name == name:
            return node
    return None


def inspect_fbx(
    path: str,
    expect_skin: bool = True,
    expect_uvs: bool = True,
    expect_normals: bool = True,
    expect_materials: bool = True,
    min_bones: int = 1,
) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    stats: Dict[str, Any] = {}
    try:
        parsed = parse_fbx(path)
    except OSError as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": [], "stats": {}, "path": path}

    stats["file_size_bytes"] = parsed["size"]
    if not parsed.get("binary"):
        return {
            "ok": False,
            "errors": ["file is not a binary FBX (ASCII FBX or another format)"],
            "warnings": [],
            "stats": stats,
            "path": path,
        }
    version = parsed.get("version")
    stats["fbx_version"] = version
    if version is None or version < 7000:
        errors.append(f"unsupported FBX version {version}")

    root = parsed["root"]
    objects = _find(root, "Objects")
    if objects is None:
        return {
            "ok": False,
            "errors": [*errors, "FBX has no Objects node"],
            "warnings": warnings,
            "stats": stats,
            "path": path,
        }

    geometry = objects.children_named("Geometry")
    models = objects.children_named("Model")
    deformers = objects.children_named("Deformer")
    materials = objects.children_named("Material")
    textures = objects.children_named("Texture")
    videos = objects.children_named("Video")

    mesh_geometries = [
        g for g in geometry if (g.props[2] if len(g.props) > 2 else b"Mesh") in (b"Mesh", "Mesh")
    ]
    stats.update(
        {
            "geometry_count": len(geometry),
            "mesh_geometry_count": len(mesh_geometries),
            "model_count": len(models),
            "deformer_count": len(deformers),
            "material_count": len(materials),
            "texture_count": len(textures),
            "embedded_texture_count": len(videos),
        }
    )
    if not mesh_geometries:
        errors.append("FBX contains no Mesh geometry")

    uv_layers = sum(len(g.children_named("LayerElementUV")) for g in mesh_geometries)
    normal_layers = sum(len(g.children_named("LayerElementNormal")) for g in mesh_geometries)
    material_layers = sum(len(g.children_named("LayerElementMaterial")) for g in mesh_geometries)
    stats["uv_layers"] = uv_layers
    stats["normal_layers"] = normal_layers
    stats["material_layers"] = material_layers
    if expect_uvs and uv_layers == 0:
        errors.append("mesh geometry has no UV layer")
    if expect_normals and normal_layers == 0:
        errors.append("mesh geometry has no normal layer")

    skins = [d for d in deformers if (d.props[2] if len(d.props) > 2 else b"") in (b"Skin", "Skin")]
    clusters = [
        d for d in deformers if (d.props[2] if len(d.props) > 2 else b"") in (b"Cluster", "Cluster")
    ]
    bone_models = [
        m
        for m in models
        if (m.props[2] if len(m.props) > 2 else b"") in (b"LimbNode", b"Bone", "LimbNode", "Bone")
    ]
    stats["skin_deformers"] = len(skins)
    stats["clusters"] = len(clusters)
    stats["bone_models"] = len(bone_models)
    stats["mesh_models"] = len(
        [m for m in models if (m.props[2] if len(m.props) > 2 else b"") in (b"Mesh", "Mesh")]
    )

    if expect_skin:
        if not skins:
            errors.append("FBX has no Skin deformer (the mesh is not skinned)")
        if len(clusters) < min_bones:
            errors.append(f"FBX has {len(clusters)} cluster(s), expected at least {min_bones}")
        if len(bone_models) < min_bones:
            errors.append(
                f"FBX has {len(bone_models)} bone model(s), expected at least {min_bones}"
            )

    if expect_materials and not materials:
        errors.append("FBX has no material")

    connections = _find(root, "Connections")
    stats["connection_count"] = len(connections.children) if connections else 0
    if connections is None or not connections.children:
        errors.append("FBX has no Connections (the scene graph is empty)")

    stats["objects"] = [m.name for m in models][:16]
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
        "path": path,
    }
