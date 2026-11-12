"""Structural + semantic validation of binary glTF (.glb) files.

This module is pure Python (no ``bpy``) so it can run in unit tests, in CI and
inside Blender. It parses the container, the JSON chunk and the binary accessor
data, then asserts the properties that make a garment usable in a game engine:

* every primitive is triangles with POSITION / NORMAL / TEXCOORD_0;
* a skin exists with joints and inverse bind matrices;
* every vertex carries JOINTS_0 / WEIGHTS_0, weights sum to 1 and at most
  ``max_influences`` of them are non-zero;
* the material references base colour, normal and metallic-roughness textures;
* those images are actually embedded in the file.
"""

from __future__ import annotations

import json
import os
import struct
from typing import Any, Dict, List, Tuple

GLB_MAGIC = b"glTF"
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

COMPONENT_TYPES = {
    5120: ("b", 1),
    5121: ("B", 1),
    5122: ("h", 2),
    5123: ("H", 2),
    5125: ("I", 4),
    5126: ("f", 4),
}

TYPE_COMPONENTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}

NORMALIZE = {
    5120: lambda v: max(v / 127.0, -1.0),
    5121: lambda v: v / 255.0,
    5122: lambda v: max(v / 32767.0, -1.0),
    5123: lambda v: v / 65535.0,
}


class GlbError(RuntimeError):
    pass


def parse_glb(path: str) -> Dict[str, Any]:
    """Split a GLB into its JSON document and binary chunk."""
    if not os.path.exists(path):
        raise GlbError(f"GLB not found: {path}")
    size = os.path.getsize(path)
    if size < 20:
        raise GlbError(f"{path} is only {size} bytes")
    with open(path, "rb") as fh:
        data = fh.read()
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != GLB_MAGIC:
        raise GlbError(f"{path} is not a GLB (magic {magic!r})")
    if version != 2:
        raise GlbError(f"{path} uses glTF container version {version}, expected 2")
    if length != len(data):
        raise GlbError(f"{path} header length {length} != file size {len(data)}")

    offset = 12
    gltf = None
    binary = b""
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk = data[offset : offset + chunk_length]
        offset += chunk_length
        if chunk_type == CHUNK_JSON:
            gltf = json.loads(chunk.decode("utf-8"))
        elif chunk_type == CHUNK_BIN:
            binary = chunk
    if gltf is None:
        raise GlbError(f"{path} has no JSON chunk")
    return {"json": gltf, "bin": binary, "size": size}


def _accessor_layout(gltf, index):
    accessors = gltf.get("accessors") or []
    if index >= len(accessors):
        raise GlbError(f"accessor {index} does not exist")
    accessor = accessors[index]
    component = accessor.get("componentType")
    if component not in COMPONENT_TYPES:
        raise GlbError(f"accessor {index} has unsupported componentType {component}")
    fmt_char, component_size = COMPONENT_TYPES[component]
    components = TYPE_COMPONENTS.get(accessor.get("type", ""), 0)
    if components == 0:
        raise GlbError(f"accessor {index} has unsupported type {accessor.get('type')!r}")
    count = int(accessor.get("count", 0))
    return accessor, component, fmt_char, component_size, components, count


def read_accessor(gltf, binary: bytes, index: int) -> List[Tuple[float, ...]]:
    """Decode an accessor into a list of numeric tuples (normalised when asked)."""
    accessor, component, fmt_char, component_size, components, count = _accessor_layout(gltf, index)
    view_index = accessor.get("bufferView")
    if view_index is None:
        return [tuple([0.0] * components) for _ in range(count)]
    views = gltf.get("bufferViews") or []
    if view_index >= len(views):
        raise GlbError(f"accessor {index} references missing bufferView {view_index}")
    view = views[view_index]
    view_offset = int(view.get("byteOffset", 0))
    stride = int(view.get("byteStride", component_size * components))
    element_size = component_size * components
    base = view_offset + int(accessor.get("byteOffset", 0))
    if base + element_size * count > len(binary) + element_size:
        raise GlbError(f"accessor {index} overruns the binary chunk")
    normalised = bool(accessor.get("normalized"))
    convert = NORMALIZE.get(component) if normalised else None
    values: List[Tuple[float, ...]] = []
    for item in range(count):
        start = base + item * stride
        if start + element_size > len(binary):
            raise GlbError(f"accessor {index} item {item} overruns the binary chunk")
        row = struct.unpack_from("<" + fmt_char * components, binary, start)
        if convert is not None:
            row = tuple(convert(v) for v in row)
        values.append(tuple(float(v) for v in row))
    return values


def inspect_glb(
    path: str,
    expect_skin: bool = True,
    expect_textures: bool = True,
    expect_normals: bool = True,
    expect_uvs: bool = True,
    max_influences: int = 4,
    sample_vertices: int = 4000,
) -> Dict[str, Any]:
    """Validate a GLB. Returns ``{"ok", "errors", "warnings", "stats"}``."""
    errors: List[str] = []
    warnings: List[str] = []
    stats: Dict[str, Any] = {}
    try:
        parsed = parse_glb(path)
        gltf = parsed["json"]
        binary = parsed["bin"]
    except (GlbError, json.JSONDecodeError, struct.error) as exc:
        return {
            "ok": False,
            "errors": [f"unreadable GLB: {exc}"],
            "warnings": [],
            "stats": {},
            "path": path,
        }

    asset = gltf.get("asset") or {}
    if asset.get("version") != "2.0":
        errors.append(f"asset.version is {asset.get('version')!r}, expected '2.0'")
    stats["generator"] = asset.get("generator")
    stats["file_size_bytes"] = parsed["size"]

    meshes = gltf.get("meshes") or []
    if not meshes:
        errors.append("no meshes in GLB")
    accessors = gltf.get("accessors") or []
    views = gltf.get("bufferViews") or []

    total_vertices = 0
    total_triangles = 0
    primitives = 0
    has_normal = False
    has_uv = False
    has_joints = False
    has_weights = False
    has_tangents = False

    for mesh_index, mesh in enumerate(meshes):
        for primitive in mesh.get("primitives", []):
            primitives += 1
            attributes = primitive.get("attributes") or {}
            mode = primitive.get("mode", 4)
            if mode != 4:
                errors.append(
                    f"mesh {mesh_index} primitive uses mode {mode}, expected TRIANGLES (4)"
                )
            if "POSITION" not in attributes:
                errors.append(f"mesh {mesh_index} primitive has no POSITION attribute")
                continue
            positions = attributes["POSITION"]
            if positions >= len(accessors):
                errors.append(f"mesh {mesh_index} POSITION accessor {positions} is missing")
                continue
            vertex_count = int(accessors[positions].get("count", 0))
            total_vertices += vertex_count
            if "NORMAL" in attributes:
                has_normal = True
            elif expect_normals:
                errors.append(f"mesh {mesh_index} primitive has no NORMAL attribute")
            if "TEXCOORD_0" in attributes:
                has_uv = True
            elif expect_uvs:
                errors.append(f"mesh {mesh_index} primitive has no TEXCOORD_0 attribute")
            if "TANGENT" in attributes:
                has_tangents = True
            if "JOINTS_0" in attributes:
                has_joints = True
            if "WEIGHTS_0" in attributes:
                has_weights = True
            if expect_skin and ("JOINTS_0" not in attributes or "WEIGHTS_0" not in attributes):
                errors.append(
                    f"mesh {mesh_index} primitive is not skinned (JOINTS_0/WEIGHTS_0 missing)"
                )

            indices = primitive.get("indices")
            if indices is not None and indices < len(accessors):
                index_count = int(accessors[indices].get("count", 0))
                if index_count % 3 != 0:
                    errors.append(
                        f"mesh {mesh_index} index count {index_count} is not a multiple of 3"
                    )
                total_triangles += index_count // 3

            if "WEIGHTS_0" in attributes:
                weights_index = attributes["WEIGHTS_0"]
                try:
                    weights = read_accessor(gltf, binary, weights_index)[:sample_vertices]
                except GlbError as exc:
                    errors.append(str(exc))
                    continue
                bad_sums = 0
                too_many = 0
                unweighted = 0
                for row in weights:
                    non_zero = [w for w in row if w > 1e-4]
                    if len(non_zero) > max_influences:
                        too_many += 1
                    total = sum(row)
                    if abs(total - 1.0) > 0.02:
                        bad_sums += 1
                    if total <= 1e-6:
                        unweighted += 1
                stats["weight_samples"] = len(weights)
                stats["weight_sum_violations"] = bad_sums
                stats["influence_violations"] = too_many
                stats["unweighted_vertices_sampled"] = unweighted
                if bad_sums:
                    errors.append(f"{bad_sums} sampled vertices have weights that do not sum to 1")
                if too_many:
                    errors.append(f"{too_many} sampled vertices exceed {max_influences} influences")
                if unweighted:
                    errors.append(f"{unweighted} sampled vertices have no skin weights")

            if expect_skin and "JOINTS_0" in attributes:
                skins = gltf.get("skins") or []
                joint_count = len(skins[0].get("joints", [])) if skins else 0
                try:
                    joints = read_accessor(gltf, binary, attributes["JOINTS_0"])[:sample_vertices]
                except GlbError as exc:
                    errors.append(str(exc))
                    continue
                out_of_range = sum(1 for row in joints if any(j >= joint_count for j in row))
                if out_of_range:
                    errors.append(
                        f"{out_of_range} sampled vertices reference a joint outside the skin"
                    )

    stats.update(
        {
            "vertices": total_vertices,
            "triangles": total_triangles,
            "primitives": primitives,
            "meshes": len(meshes),
            "has_normals": has_normal,
            "has_uvs": has_uv,
            "has_tangents": has_tangents,
            "has_joints": has_joints,
            "has_weights": has_weights,
        }
    )

    skins = gltf.get("skins") or []
    if expect_skin:
        if not skins:
            errors.append("no skin in GLB (the garment is not bound to an armature)")
        for skin_index, skin in enumerate(skins):
            if not skin.get("joints"):
                errors.append(f"skin {skin_index} has no joints")
            if skin.get("inverseBindMatrices") is None:
                errors.append(f"skin {skin_index} has no inverseBindMatrices accessor")
    stats["skins"] = len(skins)
    stats["joints"] = [len(skin.get("joints", [])) for skin in skins]

    images = gltf.get("images") or []
    embedded = [img for img in images if img.get("bufferView") is not None]
    stats["images"] = len(images)
    stats["embedded_images"] = len(embedded)
    if expect_textures:
        if not images:
            errors.append("no images in GLB (textures were not embedded)")
        elif len(embedded) != len(images):
            errors.append(f"{len(images) - len(embedded)} image(s) are external URIs, not embedded")
        for image in images:
            if image.get("bufferView") is not None and not image.get("mimeType"):
                errors.append("embedded image has no mimeType")

    materials = gltf.get("materials") or []
    stats["materials"] = len(materials)
    if not materials:
        errors.append("no materials in GLB")
    for material_index, material in enumerate(materials):
        pbr = material.get("pbrMetallicRoughness") or {}
        if expect_textures:
            if pbr.get("baseColorTexture") is None:
                errors.append(f"material {material_index} has no baseColorTexture")
            if material.get("normalTexture") is None:
                errors.append(f"material {material_index} has no normalTexture")
            if pbr.get("metallicRoughnessTexture") is None:
                errors.append(f"material {material_index} has no metallicRoughnessTexture")
    stats["textures"] = len(gltf.get("textures") or [])

    animations = gltf.get("animations") or []
    stats["animations"] = len(animations)
    for anim_index, animation in enumerate(animations):
        if not animation.get("channels"):
            errors.append(f"animation {anim_index} has no channels")
        for sampler in animation.get("samplers", []):
            for key in ("input", "output"):
                if sampler.get(key) is None:
                    errors.append(f"animation {anim_index} sampler missing {key}")

    # Accessor / bufferView consistency.
    declared = (gltf.get("buffers") or [{}])[0].get("byteLength", 0)
    stats["declared_buffer_bytes"] = declared
    stats["actual_buffer_bytes"] = len(binary)
    if declared and abs(declared - len(binary)) > 3:
        warnings.append(f"buffer byteLength {declared} != BIN chunk {len(binary)}")
    for view_index, view in enumerate(views):
        if view.get("buffer", 0) != 0:
            errors.append(
                f"bufferView {view_index} references buffer {view.get('buffer')} "
                "(only GLB buffer 0 is supported)"
            )
        if int(view.get("byteOffset", 0)) + int(view.get("byteLength", 0)) > len(binary):
            errors.append(f"bufferView {view_index} overruns the BIN chunk")

    if total_triangles == 0:
        errors.append("GLB contains no triangles")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
        "path": path,
    }
