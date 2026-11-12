"""Rig resolution and body skinning.

Two situations are supported:

1. **The base body ships with an armature** (the production case). It is used
   verbatim, and only its deform-bone vertex groups are considered for weight
   transfer.
2. **The base body has no rig at all** (what the two assets in this repository
   actually look like). A humanoid armature is generated from measured mesh
   landmarks and the body is skinned so the garment has something to bind to.

Case 2 is deliberately explicit: the stage reports ``rig_source: "procedural"``
and ``skinning_method`` in every metric, and the validation report calls the
result approximate. It is never mistaken for an authored rig.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

try:  # pragma: no cover
    import bpy
    from mathutils import Vector

    HAS_BPY = True
except ImportError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]
    Vector = None  # type: ignore[assignment]
    HAS_BPY = False

from .bpy_helpers import PipelineError, find_armature, select_only, set_active

RIG_OBJECT_NAME = "RIG_Character"

# Bone name, parent, chain from landmarks. Unity/Godot friendly names.
BONE_SPEC: List[Tuple[str, str]] = [
    ("Hips", None),
    ("Spine", "Hips"),
    ("Chest", "Spine"),
    ("Neck", "Chest"),
    ("Head", "Neck"),
    ("Shoulder.L", "Chest"),
    ("UpperArm.L", "Shoulder.L"),
    ("LowerArm.L", "UpperArm.L"),
    ("Hand.L", "LowerArm.L"),
    ("Shoulder.R", "Chest"),
    ("UpperArm.R", "Shoulder.R"),
    ("LowerArm.R", "UpperArm.R"),
    ("Hand.R", "LowerArm.R"),
    ("UpperLeg.L", "Hips"),
    ("LowerLeg.L", "UpperLeg.L"),
    ("Foot.L", "LowerLeg.L"),
    ("Toe.L", "Foot.L"),
    ("UpperLeg.R", "Hips"),
    ("LowerLeg.R", "UpperLeg.R"),
    ("Foot.R", "LowerLeg.R"),
    ("Toe.R", "Foot.R"),
]


def deform_bone_names(armature) -> List[str]:
    if armature is None or armature.type != "ARMATURE":
        return []
    return [bone.name for bone in armature.data.bones if bone.use_deform]


def has_deform_groups(obj, bone_names: List[str]) -> bool:
    if not obj.vertex_groups or not bone_names:
        return False
    names = {group.name for group in obj.vertex_groups}
    return any(name in names for name in bone_names)


def _bone_positions(landmarks: Dict[str, Any]) -> Dict[str, Tuple[Any, Any]]:
    """Head/tail of every bone in canonical world space."""
    H = landmarks["height"]
    y0 = landmarks["arm_y"]
    shoulder_z = landmarks["shoulder_z"]
    hip_z = landmarks["hip_z"]
    knee_z = landmarks["knee_z"]
    ankle_z = landmarks["ankle_z"]
    neck_z = landmarks["neck_z"]
    top_z = landmarks["top_z"]
    ground = landmarks["ground_z"]
    leg_x = landmarks["leg_x"]
    hip_x = landmarks["hip_x"]

    spine_base = hip_z
    spine_mid = hip_z + 0.10 * H
    chest_z = shoulder_z - 0.02 * H
    pelvis_y = 0.0
    positions: Dict[str, Tuple[Any, Any]] = {
        "Hips": (Vector((0, pelvis_y, spine_base)), Vector((0, pelvis_y, spine_mid))),
        "Spine": (Vector((0, pelvis_y, spine_mid)), Vector((0, 0, chest_z))),
        "Chest": (Vector((0, 0, chest_z)), Vector((0, y0, shoulder_z))),
        "Neck": (Vector((0, y0, shoulder_z)), Vector((0, landmarks["head_centre_y"], neck_z))),
        "Head": (
            Vector((0, landmarks["head_centre_y"], neck_z)),
            Vector((0, landmarks["head_centre_y"], top_z - 0.03 * H)),
        ),
    }

    for side, sign in (("L", 1.0), ("R", -1.0)):
        positions[f"Shoulder.{side}"] = (
            Vector((sign * 0.25 * landmarks["shoulder_x"], y0, shoulder_z)),
            Vector((sign * landmarks["shoulder_x"], y0, landmarks["arm_z"])),
        )
        positions[f"UpperArm.{side}"] = (
            Vector((sign * landmarks["shoulder_x"], y0, landmarks["arm_z"])),
            Vector((sign * landmarks["elbow_x"], y0, landmarks["arm_z"])),
        )
        positions[f"LowerArm.{side}"] = (
            Vector((sign * landmarks["elbow_x"], y0, landmarks["arm_z"])),
            Vector((sign * landmarks["wrist_x"], y0, landmarks["arm_z"])),
        )
        positions[f"Hand.{side}"] = (
            Vector((sign * landmarks["wrist_x"], y0, landmarks["arm_z"])),
            Vector((sign * landmarks["arm_tip_x"], y0, landmarks["arm_z"])),
        )
        positions[f"UpperLeg.{side}"] = (
            Vector((sign * hip_x, 0, hip_z)),
            Vector((sign * leg_x, 0, knee_z)),
        )
        positions[f"LowerLeg.{side}"] = (
            Vector((sign * leg_x, 0, knee_z)),
            Vector((sign * leg_x, 0, ankle_z)),
        )
        positions[f"Foot.{side}"] = (
            Vector((sign * leg_x, 0, ankle_z)),
            Vector((sign * leg_x, landmarks["toe_y"] + 0.02, ground + 0.02 * H)),
        )
        positions[f"Toe.{side}"] = (
            Vector((sign * leg_x, landmarks["toe_y"] + 0.02, ground + 0.02 * H)),
            Vector((sign * leg_x, landmarks["toe_y"] - 0.05 * H, ground + 0.02 * H)),
        )
    return positions


def build_procedural_armature(landmarks: Dict[str, Any], name: str = RIG_OBJECT_NAME):
    """Create a humanoid armature sized from measured landmarks."""
    if not HAS_BPY:
        raise PipelineError("bpy unavailable")
    armature_data = bpy.data.armatures.new(name)
    armature = bpy.data.objects.new(name, armature_data)
    bpy.context.collection.objects.link(armature)
    set_active(armature)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        positions = _bone_positions(landmarks)
        for bone_name, parent_name in BONE_SPEC:
            head, tail = positions[bone_name]
            if (tail - head).length < 1e-4:
                tail = head + Vector((0, 0, 0.02))
            bone = armature_data.edit_bones.new(bone_name)
            bone.head = head
            bone.tail = tail
            bone.use_deform = True
            # Roll so the bone's local Z leans towards the character's forward
            # (-Y). Keeps the orientation sane for exported rotations.
            direction = (tail - head).normalized()
            reference = Vector((0, -1, 0))
            if abs(direction.dot(reference)) > 0.95:
                reference = Vector((0, 0, 1))
            bone.align_roll(reference)
            if parent_name:
                bone.parent = armature_data.edit_bones[parent_name]
        # Connect the chains that meet exactly.
        for bone_name, parent_name in BONE_SPEC:
            if not parent_name:
                continue
            bone = armature_data.edit_bones[bone_name]
            parent = armature_data.edit_bones[parent_name]
            if bone.head.length and (bone.head - parent.tail).length < 1e-5:
                bone.use_connect = True
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    armature.data.display_type = "OCTAHEDRAL"
    return armature


def _distance_to_segment(point, head, tail) -> float:
    segment = tail - head
    length_sq = segment.length_squared
    if length_sq <= 1e-12:
        return (point - head).length
    t = max(0.0, min(1.0, (point - head).dot(segment) / length_sq))
    return (point - (head + segment * t)).length


def _heuristic_body_weights(
    body, armature, bone_names: List[str], limit: int = 4
) -> Dict[str, Any]:
    """Inverse-distance falloff weights when Blender's bone heat fails."""
    bones = [armature.data.bones[name] for name in bone_names if name in armature.data.bones]
    groups = {
        name: (body.vertex_groups.get(name) or body.vertex_groups.new(name=name))
        for name in bone_names
    }
    matrix = armature.matrix_world
    head_tail = [(bone.name, matrix @ bone.head_local, matrix @ bone.tail_local) for bone in bones]

    assigned = 0
    for vertex in body.data.vertices:
        point = body.matrix_world @ vertex.co
        scored = []
        for name, head, tail in head_tail:
            distance = _distance_to_segment(point, head, tail)
            scored.append((name, 1.0 / max(distance, 1e-4) ** 4))
        scored.sort(key=lambda item: item[1], reverse=True)
        chosen = scored[:limit]
        total = sum(weight for _, weight in chosen)
        if total <= 0:
            continue
        for name, weight in chosen:
            groups[name].add([vertex.index], weight / total, "REPLACE")
        assigned += 1
    return {"method": "inverse_distance", "vertices_weighted": assigned}


def _auto_body_weights(body, armature) -> Dict[str, Any]:
    """Blender's bone-heat weighting (ARMATURE_AUTO), headless safe."""
    try:
        select_only([body, armature], active=armature)
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
        weighted = sum(1 for v in body.data.vertices if len(v.groups) > 0)
        if weighted < len(body.data.vertices) * 0.98:
            raise PipelineError(
                f"bone heat left {len(body.data.vertices) - weighted} vertices unweighted"
            )
        return {"method": "bone_heat", "vertices_weighted": weighted}
    except Exception as exc:
        # Remove the failed parenting so the fallback starts clean.
        body.parent = None
        for modifier in [m for m in body.modifiers if m.type == "ARMATURE"]:
            body.modifiers.remove(modifier)
        return {"method": "bone_heat_failed", "error": str(exc), "vertices_weighted": 0}


def ensure_body_rig(body, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Return an armature bound to ``body``, creating one when necessary."""
    params = params or {}
    if not HAS_BPY:
        raise PipelineError("bpy unavailable")

    existing = find_armature(params.get("armature_object"))
    bone_names = deform_bone_names(existing)
    if existing is not None and has_deform_groups(body, bone_names):
        return {
            "armature": existing,
            "rig_source": "existing",
            "skinning_method": "authored",
            "bone_count": len(bone_names),
            "bones": bone_names,
        }

    if existing is not None and not has_deform_groups(body, bone_names):
        # An armature exists but the body is not skinned to it: skin it now.
        armature = existing
        rig_source = "existing"
    else:
        if not params.get("build_rig", True):
            raise PipelineError(
                "no armature found in the base body and build_rig is disabled; supply a "
                "rigged body variant in assets/base_body/manifest.json or set "
                'params {"build_rig": true}'
            )
        from .frame import infer_landmarks

        landmarks = params.get("_landmarks") or infer_landmarks(
            [body.matrix_world @ v.co for v in body.data.vertices]
        )
        armature = build_procedural_armature(
            landmarks, name=str(params.get("rig_name", RIG_OBJECT_NAME))
        )
        rig_source = "procedural"

    bone_names = deform_bone_names(armature)
    if not bone_names:
        raise PipelineError(f"armature {armature.name} has no deform bones")

    skinning = _auto_body_weights(body, armature)
    if skinning["vertices_weighted"] < len(body.data.vertices) * 0.98:
        # Clear partial groups from the failed heat pass, then fall back.
        for name in bone_names:
            group = body.vertex_groups.get(name)
            if group is not None:
                body.vertex_groups.remove(group)
        skinning = _heuristic_body_weights(body, armature, bone_names)
        skinning["fallback_reason"] = "bone heat weighting incomplete"
        bind_to_armature(body, armature, name="BodyArmature")
    else:
        bind_to_armature(body, armature, name="BodyArmature")

    return {
        "armature": armature,
        "rig_source": rig_source,
        "skinning_method": skinning["method"],
        "skinning_detail": skinning,
        "bone_count": len(bone_names),
        "bones": bone_names,
    }


def bind_to_armature(obj, armature, name: str = "Armature") -> None:
    """Bind ``obj`` to ``armature`` with a deform-enabled modifier + parent."""
    modifier = None
    for existing in obj.modifiers:
        if existing.type == "ARMATURE" and existing.object == armature:
            modifier = existing
            break
    if modifier is None:
        modifier = obj.modifiers.new(name=name, type="ARMATURE")
    modifier.object = armature
    modifier.use_vertex_groups = True
    modifier.use_bone_envelopes = False
    obj.parent = armature
    obj.matrix_parent_inverse = armature.matrix_world.inverted()
