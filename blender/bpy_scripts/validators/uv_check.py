"""UV + material validator: one UV layer, valid layout, complete PBR graph."""

from __future__ import annotations

from typing import Any, Dict, List

try:  # pragma: no cover
    import bpy

    _HAS_BPY = True
except ImportError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]
    _HAS_BPY = False

from ..stages.uv_material import MIN_UV_COVERAGE, _uv_report
from ..utils.logging import get_logger

logger = get_logger("validators.uv_check")

GARMENT_OBJECT_NAME = "CLOTH_Shell"


def _inspect_material(material) -> tuple[Dict[str, Any], List[str]]:
    """Check one material slot: a complete, packed Principled PBR graph."""
    problems: List[str] = []
    if not material.use_nodes:
        return {"name": material.name}, [f"material {material.name} does not use nodes"]

    nodes = material.node_tree.nodes
    image_nodes = [node for node in nodes if node.type == "TEX_IMAGE"]
    linked_inputs = set()
    bsdf = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        for socket in bsdf.inputs:
            for _link in socket.links:
                linked_inputs.add(socket.name)
    info: Dict[str, Any] = {
        "name": material.name,
        "image_nodes": [node.name for node in image_nodes],
        "images": [node.image.name for node in image_nodes if node.image],
        "linked_bsdf_inputs": sorted(linked_inputs),
        "packed": [
            bool(node.image and node.image.packed_file) for node in image_nodes if node.image
        ],
    }
    for required in ("Base Color", "Roughness", "Metallic", "Normal"):
        if required not in linked_inputs:
            problems.append(f"material input {required!r} is not texture-linked")
    if len(image_nodes) < 4:
        problems.append(f"material has {len(image_nodes)} texture nodes, expected 4 (PBR set)")
    if not all(info["packed"]):
        problems.append("material images are not packed into the .blend")
    return info, problems


def run(
    blend_path: str,
    region: str = "UPPER_BODY",
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    params = params or {}
    if not _HAS_BPY:
        return {"status": "fail", "metrics": {"error": "bpy is not available: run inside Blender"}}
    try:
        bpy.ops.wm.open_mainfile(filepath=blend_path)
    except Exception as exc:
        return {"status": "fail", "metrics": {"error": f"could not open {blend_path}: {exc}"}}

    garment = bpy.data.objects.get(params.get("garment_object", GARMENT_OBJECT_NAME))
    if garment is None or garment.type != "MESH":
        return {"status": "fail", "metrics": {"error": f"{GARMENT_OBJECT_NAME} mesh not found"}}

    uv = _uv_report(garment.data)
    problems = []
    if not uv.get("has_uv"):
        problems.append("no active UV layer")
    else:
        if uv["uv_layer_count"] != 1:
            problems.append(f"{uv['uv_layer_count']} UV layers (duplicate unwraps)")
        if uv["coverage"] < MIN_UV_COVERAGE:
            problems.append(f"UV coverage {uv['coverage']} below {MIN_UV_COVERAGE}")
        if uv["nan_loops"]:
            problems.append("NaN UV coordinates")
        if uv["loops_outside_0_1"]:
            problems.append(f"{uv['loops_outside_0_1']} UV loops outside 0..1")
        if uv["degenerate_faces"]:
            problems.append(f"{uv['degenerate_faces']} zero-area UV faces")

    # A multi-piece outfit carries one material per piece (and per detail
    # material), so every slot is inspected, not just the first one.
    slots = [slot.material for slot in garment.material_slots]
    if not slots or all(material is None for material in slots):
        problems.append("no material assigned")

    material_infos: List[Dict[str, Any]] = []
    for index, material in enumerate(slots):
        if material is None:
            problems.append(f"material slot {index} is empty")
            continue
        info, material_problems = _inspect_material(material)
        material_infos.append(info)
        if index == 0:
            problems.extend(material_problems)
        else:
            problems.extend(f"[{material.name}] {problem}" for problem in material_problems)

    metrics = {
        "uv": uv,
        "material": material_infos[0] if material_infos else {},
        "materials": material_infos,
        "problems": problems,
    }
    return {"status": "fail" if problems else "pass", "metrics": metrics}
