#!/usr/bin/env python3
"""
worker.py — headless Blender executor for the character-modification ops DSL.

    blender --background --python worker.py -- jobs/<job_id>.json \
            --out /tmp/out.glb --result /tmp/result.json

Contract
--------
* Accepts ONLY the six ops in ops_spec.md. Unknown ops hard-fail (they are also
  stripped upstream by the Cloudflare Worker; this is the second line of defence).
* Every number is clamped again here, independently of the Worker.
* Each op runs in its own try/except; failures are collected, never fatal.
* Base assets are only ever read. The only write is the exported .glb.
* Prints exactly one line of JSON as the last line of stdout, and optionally
  writes the same object to --result so CI does not have to scrape stdout.
* Self-kills after 120 s via signal.alarm (Linux runner).

Exit codes: 0 = ok (or partial success), 1 = nothing applied / fatal, 3 = timeout.
"""

import json
import math
import os
import re
import signal
import sys
import time
import traceback

import bpy  # noqa: F401  (only importable inside Blender)
import bmesh
from mathutils import Vector, Matrix

# --------------------------------------------------------------------------- #
# Contract constants (must mirror backend/src/index.js)
# --------------------------------------------------------------------------- #

MAX_OPS = 12
FACTOR_MIN, FACTOR_MAX = 0.7, 1.3
LOC_ABS_MAX = 2.0
SCALE_MIN, SCALE_MAX = 0.01, 1.0
ROT_ABS_MAX = 180.0
SELF_TIMEOUT_S = 120

ALLOWED_OPS = ("set_scale", "proportion", "bone_resize", "material_param", "add_mesh", "hair_swap")
PRIMITIVES = ("cube", "sphere", "cylinder", "cone", "plane", "torus")
HAIR_STYLES = ("long_straight", "long_wavy", "bob", "ponytail", "twin_tails", "short", "buzz", "bald", "as_is")
PARTS = ("head", "neck", "shoulders", "chest", "waist", "hips", "pelvis", "thighs",
         "legs", "calves", "arms", "hands", "feet", "height")

# normalised height bands (fraction of total body height, 0 = feet, 1 = top of head)
PART_BANDS = {
    "head": (0.86, 1.00),
    "neck": (0.80, 0.88),
    "shoulders": (0.76, 0.87),
    "chest": (0.64, 0.80),
    "waist": (0.55, 0.66),
    "hips": (0.44, 0.58),
    "pelvis": (0.42, 0.56),
    "thighs": (0.28, 0.48),
    "legs": (0.00, 0.48),
    "calves": (0.10, 0.30),
    "arms": (0.50, 0.88),
    "hands": (0.38, 0.58),
    "feet": (0.00, 0.07),
    "height": (0.00, 1.00),
}
# parts that live away from the centre line: |x| > fraction * half body width
PART_LATERAL = {"arms": 0.52, "hands": 0.68}

# limitation documented in README: band edges fade over this fraction of height
BAND_FADE = 0.06

START_TS = time.time()

# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #


def log(msg):
    print("[worker] %s" % msg, flush=True)


def clampf(value, lo, hi, default=None):
    """Best-effort float clamp. NaN/inf/junk -> default."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return max(lo, min(hi, v))


def clamp_vec(value, n, lo, hi):
    if not isinstance(value, (list, tuple)) or len(value) < n:
        return None
    out = []
    for i in range(n):
        v = clampf(value[i], lo, hi)
        if v is None:
            return None
        out.append(v)
    return out


def smoothstep(edge0, edge1, x):
    if edge0 == edge1:
        return 1.0 if x >= edge1 else 0.0
    t = (x - edge0) / (edge1 - edge0)
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def srgb_to_linear(c):
    c = max(0.0, min(1.0, c))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


NAMED_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000",
    "blue": "#0000ff", "yellow": "#ffff00", "pink": "#ffc0cb", "purple": "#800080",
    "brown": "#8b4513", "grey": "#808080", "gray": "#808080", "silver": "#c0c0c0",
    "gold": "#ffd700", "blonde": "#e8d3a0", "blond": "#e8d3a0", "orange": "#ffa500",
    "cyan": "#00ffff", "magenta": "#ff00ff", "navy": "#000080", "teal": "#008080",
}


def parse_color(value, alpha=1.0):
    """'#b22222' / '#abc' / [r,g,b] / 'red' -> linear RGBA tuple (Blender wants linear)."""
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        rgb = [clampf(v, 0.0, 1.0) for v in value[:3]]
        a = clampf(value[3], 0.0, 1.0, alpha) if len(value) > 3 else alpha
        return tuple(srgb_to_linear(v) for v in rgb) + (a,)
    if not isinstance(value, str):
        raise ValueError("colour must be a hex string like '#b22222'")
    s = value.strip().lower()
    s = NAMED_COLORS.get(s, s)
    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) == 8:
        a = int(s[6:8], 16) / 255.0
        s = s[:6]
    else:
        a = alpha
    if len(s) != 6 or re.search(r"[^0-9a-f]", s):
        raise ValueError("bad hex colour: %r" % value)
    r, g, b = (int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), a)


# --------------------------------------------------------------------------- #
# scene / name resolution
# --------------------------------------------------------------------------- #


class Ctx(object):
    def __init__(self, manifest, job):
        self.manifest = manifest or {}
        self.job = job
        self.armature = None
        self.meshes = []
        self.body_meshes = []
        self.warnings = []
        self.created = []
        self.license = "unknown"

    def warn(self, msg):
        self.warnings.append(msg)
        log("WARN " + msg)


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def find_object(name, objects=None, types=None):
    """Exact -> case-insensitive -> normalised -> prefix match. Tolerates .001 suffixes."""
    if not name:
        return None
    pool = [o for o in (objects if objects is not None else bpy.context.scene.objects)
            if types is None or o.type in types]
    for o in pool:
        if o.name == name:
            return o
    low = str(name).lower()
    for o in pool:
        if o.name.lower() == low:
            return o
    n = _norm(name)
    for o in pool:
        if _norm(re.sub(r"\.\d+$", "", o.name)) == n:
            return o
    for o in pool:
        if o.name.lower().startswith(low):
            return o
    return None


def find_material(name):
    if not name:
        return None
    mats = list(bpy.data.materials)
    for m in mats:
        if m.name == name:
            return m
    low = str(name).lower()
    for m in mats:
        if m.name.lower() == low:
            return m
    n = _norm(name)
    for m in mats:
        if _norm(re.sub(r"\.\d+$", "", m.name)) == n:
            return m
    for m in mats:
        if m.name.lower().startswith(low):
            return m
    return None


def find_bone_data(arm, name):
    if arm is None or not name:
        return None
    bones = arm.data.bones
    b = bones.get(name)
    if b:
        return b
    low = str(name).lower()
    for b in bones:
        if b.name.lower() == low:
            return b
    n = _norm(name)
    for b in bones:
        if _norm(b.name) == n:
            return b
    return None


def bones_by_prefix(arm, prefix):
    if arm is None:
        return []
    p = str(prefix).lower().strip()
    exact = [b for b in arm.data.bones if b.name.lower() == p]
    if exact:
        return exact
    return [b for b in arm.data.bones if b.name.lower().startswith(p) or _norm(b.name).startswith(_norm(p))]


def to_object_mode():
    try:
        obj = bpy.context.view_layer.objects.active
        if obj is not None and obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception as exc:  # pragma: no cover - defensive
        log("mode_set(OBJECT) failed: %s" % exc)


def ensure_active(obj):
    to_object_mode()
    try:
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
    except Exception as exc:  # pragma: no cover
        log("could not activate %s: %s" % (getattr(obj, "name", "?"), exc))
    return obj


def collection_of(obj):
    for coll in obj.users_collection:
        return coll
    return bpy.context.scene.collection


# --------------------------------------------------------------------------- #
# vertex access that survives shape keys
# --------------------------------------------------------------------------- #


def _vert_store(obj):
    """Return (count, getter, setter). Edits the basis shape key when present."""
    keys = obj.data.shape_keys
    if keys is not None and len(keys.key_blocks) > 0:
        seq = keys.key_blocks[0].data
        return len(seq), (lambda i: seq[i].co), (lambda i, c: seq[i].co.__setitem__(slice(None), c))
    seq = obj.data.vertices
    return len(seq), (lambda i: seq[i].co), (lambda i, c: seq[i].co.__setitem__(slice(None), c))


def transform_world(obj, fn):
    """Apply fn(Vector world position) -> Vector for every vertex of obj."""
    mw = obj.matrix_world.copy()
    inv = mw.inverted()
    count, get, put = _vert_store(obj)
    for i in range(count):
        put(i, inv @ fn(mw @ get(i)))
    obj.data.update()


def world_bounds(objects):
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for obj in objects:
        mw = obj.matrix_world
        for v in obj.data.vertices:
            w = mw @ v.co
            for k in range(3):
                lo[k] = min(lo[k], w[k])
                hi[k] = max(hi[k], w[k])
    if lo.x > hi.x:
        return Vector((-0.5, -0.5, 0.0)), Vector((0.5, 0.5, 1.7))
    return lo, hi


def body_metrics(ctx):
    lo, hi = world_bounds(ctx.body_meshes or ctx.meshes)
    return {
        "min": lo,
        "max": hi,
        "height": max(1e-6, hi.z - lo.z),
        "center_x": (lo.x + hi.x) * 0.5,
        "center_y": (lo.y + hi.y) * 0.5,
        "half_width": max(1e-6, (hi.x - lo.x) * 0.5),
    }


def is_body_mesh(obj, ctx):
    if ctx.armature is None:
        return True
    if obj.parent == ctx.armature:
        return True
    for mod in obj.modifiers:
        if mod.type == "ARMATURE" and mod.object == ctx.armature:
            return True
    return False


# --------------------------------------------------------------------------- #
# ops
# --------------------------------------------------------------------------- #


def op_set_scale(ctx, op):
    """Scale one mesh (or the whole body) along an axis, anchored at its bottom-centre."""
    target = op.get("target") or "Body"
    axis = str(op.get("axis") or "all").lower()
    if axis not in ("x", "y", "z", "all"):
        raise ValueError("axis must be x|y|z|all, got %r" % axis)
    factor = clampf(op.get("factor"), FACTOR_MIN, FACTOR_MAX)
    if factor is None:
        raise ValueError("factor missing or not a number")

    if str(target).lower() in ("body", "all", "*", ""):
        objs = list(ctx.body_meshes or ctx.meshes)
    else:
        obj = find_object(target, ctx.meshes)
        if obj is None:
            raise ValueError("mesh %r not found in the base model" % target)
        objs = [obj]
    if not objs:
        raise ValueError("no target meshes")

    lo, hi = world_bounds(objs)
    pivot = Vector(((lo.x + hi.x) * 0.5, (lo.y + hi.y) * 0.5, lo.z))
    vec = Vector((factor if axis in ("x", "all") else 1.0,
                  factor if axis in ("y", "all") else 1.0,
                  factor if axis in ("z", "all") else 1.0))
    for obj in objs:
        transform_world(obj, lambda w, p=pivot, s=vec: Vector((
            p.x + (w.x - p.x) * s.x,
            p.y + (w.y - p.y) * s.y,
            p.z + (w.z - p.z) * s.z,
        )))
    return {"targets": [o.name for o in objs], "axis": axis, "factor": round(factor, 4)}


def _band_weight(t, lo, hi, lateral=None, x_rel=0.0):
    w = smoothstep(lo - BAND_FADE, lo, t) * (1.0 - smoothstep(hi, hi + BAND_FADE, t))
    if lateral is not None and x_rel < lateral:
        w *= 0.0
    return w


def op_proportion(ctx, op):
    """Smooth regional body scaling. The workhorse for silhouette changes."""
    part = str(op.get("part") or "").lower()
    if part not in PART_BANDS:
        raise ValueError("part must be one of %s" % ", ".join(PARTS))
    width = clampf(op.get("width"), FACTOR_MIN, FACTOR_MAX, 1.0)
    height = clampf(op.get("height"), FACTOR_MIN, FACTOR_MAX, width)
    depth = clampf(op.get("depth"), FACTOR_MIN, FACTOR_MAX, width)

    objs = list(ctx.body_meshes or ctx.meshes)
    if not objs:
        raise ValueError("no meshes to modify")

    m = body_metrics(ctx)
    z0, h = m["min"].z, m["height"]
    lo_t, hi_t = PART_BANDS[part]
    lateral = PART_LATERAL.get(part)
    pivot_z = z0 + h * (lo_t + hi_t) * 0.5
    cx, cy = m["center_x"], m["center_y"]

    touched = 0
    for obj in objs:
        def fn(w, m=m, z0=z0, h=h, lo_t=lo_t, hi_t=hi_t, lateral=lateral,
               cx=cx, cy=cy, pz=pivot_z, width=width, height=height, depth=depth):
            t = (w.z - z0) / h
            x_rel = abs(w.x - cx) / m["half_width"]
            bw = _band_weight(t, lo_t, hi_t, lateral, x_rel)
            if bw <= 0.0005:
                return w
            fx = 1.0 + (width - 1.0) * bw
            fy = 1.0 + (depth - 1.0) * bw
            fz = 1.0 + (height - 1.0) * bw
            return Vector((cx + (w.x - cx) * fx,
                           cy + (w.y - cy) * fy,
                           pz + (w.z - pz) * fz))

        transform_world(obj, fn)
        touched += 1

    bone_moves = _mirror_bones_in_band(ctx, lo_t, hi_t, lateral, z0, h, cx, cy, pivot_z,
                                       width, depth, height)
    return {"part": part, "width": round(width, 4), "height": round(height, 4),
            "depth": round(depth, 4), "meshes": len(objs), "bones": bone_moves}


def _mirror_bones_in_band(ctx, lo_t, hi_t, lateral, z0, h, cx, cy, pivot_z, fx, fy, fz):
    """Keep the rig roughly aligned with the region we just scaled."""
    arm = ctx.armature
    if arm is None:
        return 0
    half_w = max(1e-6, body_metrics(ctx)["half_width"])
    moved = 0
    try:
        ensure_active(arm)
        bpy.ops.object.mode_set(mode="EDIT")
        mw = arm.matrix_world.copy()
        inv = mw.inverted()
        for eb in arm.data.edit_bones:
            head_w = mw @ eb.head
            t = (head_w.z - z0) / h
            bw = _band_weight(t, lo_t, hi_t, lateral, abs(head_w.x - cx) / half_w)
            if bw <= 0.0005:
                continue
            sx = 1.0 + (fx - 1.0) * bw
            sy = 1.0 + (fy - 1.0) * bw
            sz = 1.0 + (fz - 1.0) * bw

            def xf(v):
                return inv @ Vector((cx + (v.x - cx) * sx,
                                     cy + (v.y - cy) * sy,
                                     pivot_z + (v.z - pivot_z) * sz))

            head = xf(head_w)
            tail = xf(mw @ eb.tail)
            eb.head = head
            eb.tail = tail
            moved += 1
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception as exc:
        to_object_mode()
        ctx.warn("bone mirroring skipped: %s" % exc)
    return moved


def op_bone_resize(ctx, op):
    """Resize a limb: bone rest length + the vertices weighted to it."""
    prefix = op.get("bone_prefix") or op.get("bone") or ""
    factor = clampf(op.get("factor"), FACTOR_MIN, FACTOR_MAX)
    if factor is None:
        raise ValueError("factor missing or not a number")
    if not str(prefix).strip():
        raise ValueError("bone_prefix required")
    arm = ctx.armature
    if arm is None:
        ctx.warn("no armature — bone_resize skipped (static mesh base)")
        return {"skipped": "no armature"}

    bones = bones_by_prefix(arm, prefix)
    if not bones:
        raise ValueError("no bone matches prefix %r" % prefix)

    names = [b.name for b in bones]
    heads = {b.name: (arm.matrix_world @ b.head_local) for b in bones}

    # 1) vertices weighted to those bones move with the bone
    moved_verts = 0
    for obj in (ctx.body_meshes or ctx.meshes):
        index_to_name = {vg.index: vg.name for vg in obj.vertex_groups}
        wanted = {vg.index for vg in obj.vertex_groups if vg.name in heads}
        if not wanted:
            continue
        count, get, put = _vert_store(obj)
        verts = obj.data.vertices
        mw = obj.matrix_world.copy()
        inv = mw.inverted()
        for vi in range(count):
            best, bone_name = 0.0, None
            for g in verts[vi].groups:
                if g.group in wanted and g.weight > best:
                    best, bone_name = g.weight, index_to_name.get(g.group)
            if bone_name is None or bone_name not in heads or best < 0.35:
                continue
            hw = heads[bone_name]
            w = mw @ get(vi)
            put(vi, inv @ (hw + (w - hw) * factor))
            moved_verts += 1
        obj.data.update()

    # 2) bones themselves (length follows the factor)
    try:
        ensure_active(arm)
        bpy.ops.object.mode_set(mode="EDIT")
        for eb in arm.data.edit_bones:
            if eb.name in names:
                d = (eb.tail - eb.head)
                if d.length > 1e-6:
                    eb.tail = eb.head + d * factor
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception as exc:
        to_object_mode()
        ctx.warn("bone length update failed: %s" % exc)

    return {"bones": names, "factor": round(factor, 4), "vertices": moved_verts}


COLOR_PARAMS = ("base color", "emission color")
PRINCIPLED_ALIASES = {
    "base color": "Base Color", "basecolour": "Base Color", "basecolor": "Base Color",
    "diffuse": "Base Color", "albedo": "Base Color", "color": "Base Color", "colour": "Base Color",
    "metallic": "Metallic", "metalness": "Metallic",
    "roughness": "Roughness",
    "alpha": "Alpha", "opacity": "Alpha",
    "emission": "Emission Color", "emissive": "Emission Color", "emission color": "Emission Color",
    "emission strength": "Emission Strength",
    "specular": "Specular IOR Level", "specular ior level": "Specular IOR Level",
    "transmission": "Transmission Weight", "transmission weight": "Transmission Weight",
    "coat": "Coat Weight", "clearcoat": "Coat Weight", "coat weight": "Coat Weight",
    "sheen": "Sheen Weight", "sheen weight": "Sheen Weight",
    "ior": "IOR",
    "subsurface": "Subsurface Weight", "subsurface weight": "Subsurface Weight",
    "normal strength": "Normal Strength", "normal": "Normal Strength",
}
PARAM_RANGES = {
    "Metallic": (0.0, 1.0), "Roughness": (0.0, 1.0), "Alpha": (0.0, 1.0),
    "Emission Strength": (0.0, 5.0), "Specular IOR Level": (0.0, 1.0),
    "Transmission Weight": (0.0, 1.0), "Coat Weight": (0.0, 1.0),
    "Sheen Weight": (0.0, 1.0), "Subsurface Weight": (0.0, 1.0),
    "IOR": (1.0, 2.5), "Normal Strength": (0.0, 2.0),
}


def _principled(mat):
    if not mat.use_nodes or not mat.node_tree:
        mat.use_nodes = True
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return None


def op_material_param(ctx, op):
    mat_name = op.get("mat") or op.get("material") or ""
    param_raw = str(op.get("param") or "").strip().lower()
    value = op.get("value")

    declared = [m for m in (ctx.manifest.get("materials") or []) if m]
    if declared and not any(_norm(d) == _norm(mat_name) for d in declared):
        raise ValueError("material %r is not in the manifest" % mat_name)

    mat = find_material(mat_name)
    if mat is None:
        raise ValueError("material %r not found" % mat_name)

    param = PRINCIPLED_ALIASES.get(param_raw)
    if param is None:
        raise ValueError("unsupported param %r" % param_raw)

    node = _principled(mat)
    if node is None:
        raise ValueError("material %r has no Principled BSDF" % mat.name)

    if param in ("Base Color", "Emission Color"):
        rgba = parse_color(value)
        socket = node.inputs.get(param)
        if socket is None:
            raise ValueError("%s socket missing on %r" % (param, mat.name))
        if param == "Base Color" and socket.is_linked:
            for link in list(socket.links):
                mat.node_tree.links.remove(link)
        socket.default_value = rgba
        label = "#%02x%02x%02x" % tuple(int(round(srgb_to_linear_inv(c) * 255)) for c in rgba[:3])
        return {"mat": mat.name, "param": param, "value": label}

    if param == "Normal Strength":
        target = node.inputs.get("Normal")
        if target is None or not target.is_linked:
            raise ValueError("material %r has no normal map to adjust" % mat.name)
        nm = target.links[0].from_node
        strength = nm.inputs.get("Strength")
        if strength is None:
            raise ValueError("normal map node has no Strength input")
        lo, hi = PARAM_RANGES[param]
        strength.default_value = clampf(value, lo, hi, 1.0)
        return {"mat": mat.name, "param": param, "value": round(strength.default_value, 4)}

    lo, hi = PARAM_RANGES.get(param, (0.0, 1.0))
    num = clampf(value, lo, hi)
    if num is None:
        raise ValueError("%s needs a number, got %r" % (param, value))
    socket = node.inputs.get(param)
    if socket is None:
        raise ValueError("%s socket missing on %r" % (param, mat.name))
    socket.default_value = num
    return {"mat": mat.name, "param": param, "value": round(num, 4)}


def srgb_to_linear_inv(c):
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def op_add_mesh(ctx, op):
    prim = str(op.get("primitive") or "cube").lower()
    if prim not in PRIMITIVES:
        raise ValueError("primitive must be one of %s" % ", ".join(PRIMITIVES))
    raw_loc = op.get("loc") or [0.0, 0.0, 1.0]
    raw_scale = op.get("scale") or [0.1, 0.1, 0.1]
    loc = clamp_vec(raw_loc, 3, -LOC_ABS_MAX, LOC_ABS_MAX)
    scale = clamp_vec(raw_scale, 3, SCALE_MIN, SCALE_MAX)
    if loc is None:
        raise ValueError("loc must be [x,y,z] numbers")
    if scale is None:
        raise ValueError("scale must be [x,y,z] numbers")
    rot = clamp_vec(op.get("rot") or [0.0, 0.0, 0.0], 3, -ROT_ABS_MAX, ROT_ABS_MAX) or [0.0, 0.0, 0.0]

    name = str(op.get("name") or ("Mesh_%d" % int(time.time() * 1000)))
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)[:40] or "AddMesh"
    if find_object(name, ctx.meshes) is not None:
        name = "%s_%d" % (name, int(time.time() * 1000) % 100000)

    to_object_mode()
    if prim == "cube":
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    elif prim == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=16, radius=0.5, location=loc)
    elif prim == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.5, depth=1.0, location=loc)
    elif prim == "cone":
        bpy.ops.mesh.primitive_cone_add(vertices=24, radius1=0.5, radius2=0.0, depth=1.0, location=loc)
    elif prim == "plane":
        bpy.ops.mesh.primitive_plane_add(size=1.0, location=loc)
    else:  # torus
        bpy.ops.mesh.primitive_torus_add(major_radius=0.4, minor_radius=0.1, location=loc)

    obj = bpy.context.view_layer.objects.active
    obj.name = name
    obj.data.name = name
    obj.scale = scale
    obj.rotation_euler = [math.radians(a) for a in rot]

    if prim in ("sphere", "cylinder", "cone", "torus"):
        try:
            bpy.ops.object.shade_smooth()
        except Exception:
            pass

    mat = None
    if op.get("mat"):
        mat = find_material(op.get("mat"))
        if mat is None:
            ctx.warn("add_mesh %r: material %r not found, left unassigned" % (name, op.get("mat")))
        else:
            obj.data.materials.clear()
            obj.data.materials.append(mat)

    if ctx.armature is not None:
        obj.parent = ctx.armature
        obj.matrix_parent_inverse = ctx.armature.matrix_world.inverted()

    ctx.created.append(obj)
    ctx.meshes.append(obj)
    return {"name": name, "primitive": prim, "loc": [round(v, 3) for v in loc],
            "scale": [round(v, 3) for v in scale], "mat": mat.name if mat else None}


def _hair_styles(ctx):
    hair = ctx.manifest.get("hair") or {}
    return hair.get("styles") or {}, [m for m in (hair.get("meshes") or []) if m]


def _hair_targets(ctx):
    hair = ctx.manifest.get("hair") or {}
    listed = [m for m in (hair.get("meshes") or []) if m]
    objs = []
    for name in listed:
        o = find_object(name, ctx.meshes)
        if o is not None:
            objs.append(o)
    if not objs:
        objs = [o for o in ctx.meshes if re.search(r"hair", o.name, re.I)]
    return objs


def _head_anchor(ctx, metrics):
    arm = ctx.armature
    head_bone = (ctx.manifest.get("hair") or {}).get("head_bone") or "head"
    if arm is not None:
        b = find_bone_data(arm, head_bone)
        if b is not None:
            head_w = arm.matrix_world @ b.head_local
            tail_w = arm.matrix_world @ b.tail_local
            length = max(0.12, (tail_w - head_w).length)
            center = head_w + (tail_w - head_w) * 0.55
            return center, length
    h = metrics["height"]
    length = max(0.12, h * 0.135)
    center = Vector((metrics["center_x"], metrics["center_y"], metrics["max"].z - length * 0.55))
    return center, length


def _hair_material(ctx, color, style):
    name = "Hair_%s" % style
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    node = _principled(mat)
    if node is not None:
        node.inputs["Base Color"].default_value = parse_color(color)
        if node.inputs.get("Roughness"):
            node.inputs["Roughness"].default_value = 0.45
        if node.inputs.get("Specular IOR Level"):
            node.inputs["Specular IOR Level"].default_value = 0.35
    mat.diffuse_color = parse_color(color)
    return mat


def _assign_material(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def _hair_cap(center, radius, mat, back_sign, cutoff=-0.42, name="Hair_Cap"):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=28, ring_count=18, radius=radius, location=center)
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    obj.data.name = name
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    doomed = [v for v in bm.verts if v.co.z < cutoff * radius]
    bmesh.ops.delete(bm, geom=doomed, context="VERTS")
    bm.to_mesh(me)
    bm.free()
    try:
        bpy.ops.object.shade_smooth()
    except Exception:
        pass
    _assign_material(obj, mat)
    obj.scale = (1.10, 1.16, 1.06)
    obj.location = center + Vector((0.0, back_sign * radius * 0.16, radius * 0.10))
    return obj


def _hair_mass(center, radius, length, mat, back_sign, name="Hair_Mass",
               top_scale=1.9, tip_scale=1.05, flat=0.62, tilt=0.0, wave=0.0):
    bpy.ops.mesh.primitive_cone_add(
        vertices=20,
        radius1=radius * tip_scale,
        radius2=radius * top_scale,
        depth=length,
        location=(center.x, center.y + back_sign * radius * 0.85, center.z - length * 0.5),
        rotation=(math.radians(tilt), 0.0, 0.0),
    )
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    obj.data.name = name
    obj.scale = (1.0, flat, 1.0)
    _assign_material(obj, mat)
    try:
        bpy.ops.object.shade_smooth()
    except Exception:
        pass
    if wave:
        base_z = obj.location.z

        def ripple(w):
            phase = (w.z - base_z) / max(length, 1e-6)
            return Vector((w.x + math.sin(phase * math.pi * 5.0) * wave, w.y, w.z))

        transform_world(obj, ripple)
    return obj


def op_hair_swap(ctx, op):
    style = str(op.get("style") or "as_is").lower()
    if style not in HAIR_STYLES:
        raise ValueError("style must be one of %s" % ", ".join(HAIR_STYLES))
    color = op.get("color") or "#2b1d16"
    metrics = body_metrics(ctx)
    center, head_len = _head_anchor(ctx, metrics)
    radius = max(0.05, head_len * 0.58)
    forward = (ctx.manifest.get("hair") or {}).get("forward_axis") or ctx.manifest.get("forward_axis") or "-Y"
    back_sign = -1.0 if str(forward).strip().upper().startswith("+") else 1.0

    existing = _hair_targets(ctx)
    styles, _ = _hair_styles(ctx)
    originals = {o.name: o for o in existing}

    # Preferred path: the base ships real hair meshes per style.
    if styles:
        wanted = styles.get(style)
        chosen = find_object(wanted, ctx.meshes) if wanted else None
        if chosen is not None:
            mat = _hair_material(ctx, color, style)
            _assign_material(chosen, mat)
            hidden = []
            for o in existing:
                if o != chosen:
                    hidden.append(o.name)
                    bpy.data.objects.remove(o, do_unlink=True)
                    if o in ctx.meshes:
                        ctx.meshes.remove(o)
            return {"style": style, "mode": "manifest", "hair": chosen.name, "removed": hidden}

    if style == "as_is":
        mat = _hair_material(ctx, color, style)
        for o in existing:
            _assign_material(o, mat)
        return {"style": "as_is", "mode": "recolor", "meshes": list(originals.keys())}

    # Otherwise: remove old hair, build a stylised proxy.
    removed = []
    for o in existing:
        removed.append(o.name)
        bpy.data.objects.remove(o, do_unlink=True)
        if o in ctx.meshes:
            ctx.meshes.remove(o)

    mat = _hair_material(ctx, color, style)
    built = []
    h = metrics["height"]

    if style == "bald":
        return {"style": style, "mode": "procedural", "removed": removed, "created": []}

    with_hair = style in ("short", "buzz", "bob", "long_straight", "long_wavy", "ponytail", "twin_tails")
    if with_hair:
        cutoff = -0.62 if style == "buzz" else -0.42
        cap = _hair_cap(center, radius * (1.01 if style == "buzz" else 1.05), mat, back_sign, cutoff)
        ctx.created.append(cap)
        ctx.meshes.append(cap)
        built.append(cap.name)

    lengths = {
        "bob": 0.10 * h,
        "long_straight": 0.30 * h,
        "long_wavy": 0.32 * h,
        "ponytail": 0.24 * h,
        "twin_tails": 0.20 * h,
        "short": 0.05 * h,
        "buzz": 0.0,
    }
    length = lengths.get(style, 0.0)

    if style in ("bob", "long_straight", "long_wavy", "short") and length > 0:
        mass = _hair_mass(center, radius, length, mat, back_sign,
                          name="Hair_Back", flat=0.62,
                          wave=radius * 0.55 if style == "long_wavy" else 0.0)
        ctx.created.append(mass)
        ctx.meshes.append(mass)
        built.append(mass.name)

    if style == "ponytail" and length > 0:
        tie = Vector((center.x, center.y + back_sign * radius * 1.15, center.z + radius * 0.15))
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=10, radius=radius * 0.42, location=tie)
        knot = bpy.context.view_layer.objects.active
        knot.name = "Hair_Tie"
        knot.data.name = "Hair_Tie"
        _assign_material(knot, mat)
        ctx.created.append(knot)
        ctx.meshes.append(knot)
        built.append(knot.name)
        tail = _hair_mass(tie, radius * 0.85, length, mat, back_sign,
                          name="Hair_Ponytail", top_scale=1.35, tip_scale=0.35,
                          flat=0.85, tilt=-22.0)
        tail.location = Vector((tie.x, tie.y + back_sign * radius * 0.30, tie.z - length * 0.42))
        ctx.created.append(tail)
        ctx.meshes.append(tail)
        built.append(tail.name)

    if style == "twin_tails" and length > 0:
        for side, sx in (("L", 1.0), ("R", -1.0)):
            anchor = Vector((center.x + sx * radius * 1.15,
                             center.y + back_sign * radius * 0.55,
                             center.z + radius * 0.05))
            tail = _hair_mass(anchor, radius * 0.72, length, mat, back_sign,
                              name="Hair_Tail_%s" % side, top_scale=1.25, tip_scale=0.30,
                              flat=0.85, tilt=0.0)
            tail.location = Vector((anchor.x + sx * radius * 0.15,
                                    anchor.y + back_sign * radius * 0.2,
                                    anchor.z - length * 0.45))
            tail.rotation_euler = (math.radians(-14.0 * back_sign), math.radians(8.0 * sx), 0.0)
            ctx.created.append(tail)
            ctx.meshes.append(tail)
            built.append(tail.name)

    return {"style": style, "mode": "procedural", "removed": removed,
            "created": built, "radius": round(radius, 4)}


OPS = {
    "set_scale": op_set_scale,
    "proportion": op_proportion,
    "bone_resize": op_bone_resize,
    "material_param": op_material_param,
    "add_mesh": op_add_mesh,
    "hair_swap": op_hair_swap,
}


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #


def parse_argv(argv):
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = [a for a in argv[1:] if a.endswith(".json")]
    job_path = None
    out = "/tmp/out.glb"
    result = None
    base = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]
            i += 2
        elif a == "--result" and i + 1 < len(argv):
            result = argv[i + 1]
            i += 2
        elif a == "--base" and i + 1 < len(argv):
            base = argv[i + 1]
            i += 2
        elif a.startswith("--"):
            i += 1
        else:
            job_path = a
            i += 1
    return job_path, out, result, base


def read_license(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            head = fh.read(400)
    except OSError:
        return "unknown"
    m = re.search(r"commercial\s*:\s*(true|false|unknown)", head, re.I)
    return m.group(1).lower() if m else "unknown"


def load_manifest(asset_id, base_dir):
    candidates = [
        os.path.join(base_dir, "manifest.json"),
        os.path.join("bases", asset_id, "manifest.json"),
        os.path.join("bases", asset_id, "model.manifest.json"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                data.pop("notes", None)
                return data, path
            except Exception as exc:
                log("manifest %s unreadable: %s" % (path, exc))
    return {}, None


def resolve_base(job, asset_id, base_dir, explicit):
    candidates = []
    if explicit:
        candidates.append(explicit)
    if job.get("base_path"):
        candidates.append(job["base_path"])
    if base_dir:
        candidates.extend([
            os.path.join(base_dir, job.get("model") or "model.gltf"),
            os.path.join(base_dir, "model.gltf"),
            os.path.join(base_dir, "model.glb"),
        ])
    candidates.extend([
        os.path.join("bases", asset_id, "model.gltf"),
        os.path.join("bases", asset_id, "model.glb"),
    ])
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


class TimeoutError_(Exception):
    pass


def _on_alarm(signum, frame):
    raise TimeoutError_("worker self-timeout after %ds" % SELF_TIMEOUT_S)


def setup_watchdog():
    try:
        signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(SELF_TIMEOUT_S)
        return True
    except Exception as exc:  # pragma: no cover - non-Unix
        log("watchdog unavailable: %s" % exc)
        return False


def reset_scene():
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    except Exception:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)


def export_glb(path):
    kwargs = dict(
        filepath=path,
        export_format="GLB",
        export_skins=True,
        export_morph=True,
        export_apply=True,
        export_yup=True,
        use_selection=False,
        export_cameras=False,
        export_lights=False,
    )
    try:
        bpy.ops.export_scene.gltf(**kwargs)
    except TypeError:
        # older/newer exporters reject unknown kwargs
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB")


def emit(result, result_path):
    line = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
    if result_path:
        try:
            with open(result_path, "w", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            log("could not write %s: %s" % (result_path, exc))
    print(line, flush=True)


def main():
    job_path, out_path, result_path, explicit_base = parse_argv(list(sys.argv))
    result = {
        "ok": False,
        "job_id": None,
        "asset_id": None,
        "applied": [],
        "errors": [],
        "warnings": [],
        "output": None,
        "bytes": 0,
        "seconds": 0.0,
    }

    watchdog = setup_watchdog()

    try:
        if not job_path or not os.path.isfile(job_path):
            result["errors"].append({"op": "__job__", "error": "job file not found: %r" % job_path})
            return finish(result, result_path, 1)

        with open(job_path, "r", encoding="utf-8") as fh:
            job = json.load(fh)

        asset_id = str(job.get("asset_id") or "female_base_v3")
        result["job_id"] = job.get("job_id") or os.path.splitext(os.path.basename(job_path))[0]
        result["asset_id"] = asset_id

        base_path = resolve_base(job, asset_id, os.path.join("bases", asset_id), explicit_base)
        if not base_path:
            result["errors"].append({"op": "__base__", "error": "base model not found for %r" % asset_id})
            return finish(result, result_path, 1)

        manifest, manifest_path = load_manifest(asset_id, os.path.join("bases", asset_id))
        ctx = Ctx(manifest, job)
        ctx.license = read_license(os.path.join(os.path.dirname(base_path), "LICENSE"))
        result["license"] = ctx.license
        if ctx.license == "false":
            ctx.warn("license says commercial: false — do not ship this output")
        if manifest_path is None:
            ctx.warn("no manifest.json for %s — name matching will be fuzzy" % asset_id)

        log("base=%s manifest=%s" % (base_path, manifest_path))
        reset_scene()
        t0 = time.time()
        try:
            if base_path.lower().endswith(".glb"):
                bpy.ops.import_scene.gltf(filepath=base_path)
            else:
                bpy.ops.import_scene.gltf(filepath=base_path)
        except Exception as exc:
            result["errors"].append({"op": "__import__", "error": "%s" % exc})
            return finish(result, result_path, 1)
        log("imported in %.1fs" % (time.time() - t0))

        to_object_mode()
        ctx.meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
        want_arm = manifest.get("armature")
        ctx.armature = find_object(want_arm, arms) if want_arm else (arms[0] if arms else None)
        ctx.body_meshes = [o for o in ctx.meshes if is_body_mesh(o, ctx)] or list(ctx.meshes)
        if manifest.get("skinning") is False:
            ctx.warn("manifest says skinning:false — rig ops will no-op")
        log("meshes=%d armature=%s" % (len(ctx.meshes), ctx.armature.name if ctx.armature else "none"))

        ops = job.get("ops")
        if not isinstance(ops, list):
            result["errors"].append({"op": "__ops__", "error": "job.ops must be an array"})
            return finish(result, result_path, 1)
        if len(ops) > MAX_OPS:
            ctx.warn("job has %d ops, only the first %d are executed" % (len(ops), MAX_OPS))
            ops = ops[:MAX_OPS]

        for index, op in enumerate(ops):
            name = (op or {}).get("op") if isinstance(op, dict) else None
            if not isinstance(op, dict):
                result["errors"].append({"op": "op[%d]" % index, "error": "not an object"})
                continue
            if name not in OPS:
                # hard fail per the contract
                result["errors"].append({"index": index, "op": name or "op[%d]" % index,
                                         "error": "unknown op %r (allowed: %s)" % (name, ", ".join(ALLOWED_OPS))})
                continue
            try:
                info = OPS[name](ctx, op)
                to_object_mode()
                result["applied"].append({"index": index, "op": name, "result": info})
                log("applied %s %s" % (name, json.dumps(info, default=str)[:240]))
            except TimeoutError_:
                raise
            except Exception as exc:
                result["errors"].append({"index": index, "op": name, "error": "%s" % exc})
                log("FAILED %s: %s\n%s" % (name, exc, traceback.format_exc(limit=3)))

        result["warnings"] = ctx.warnings
        result["scene"] = {
            "meshes": len(ctx.meshes),
            "armature": ctx.armature.name if ctx.armature else None,
            "created": [o.name for o in ctx.created if hasattr(o, "name")],
        }

        if not result["applied"]:
            return finish(result, result_path, 1)

        to_object_mode()
        try:
            bpy.ops.object.select_all(action="DESELECT")
        except Exception:
            pass
        os.makedirs(os.path.dirname(out_path) or "/tmp", exist_ok=True)
        t0 = time.time()
        export_glb(out_path)
        if not os.path.isfile(out_path):
            result["errors"].append({"op": "__export__", "error": "exporter produced no file"})
            return finish(result, result_path, 1)
        result["output"] = out_path
        result["bytes"] = os.path.getsize(out_path)
        log("exported %s (%.1f KB) in %.1fs" % (out_path, result["bytes"] / 1024.0, time.time() - t0))
        result["ok"] = True
        return finish(result, result_path, 0)

    except TimeoutError_ as exc:
        result["errors"].append({"op": "__timeout__", "error": "%s" % exc})
        return finish(result, result_path, 3)
    except Exception as exc:
        result["errors"].append({"op": "__fatal__", "error": "%s" % exc})
        log(traceback.format_exc())
        return finish(result, result_path, 1)
    finally:
        if watchdog:
            try:
                signal.alarm(0)
            except Exception:
                pass


def finish(result, result_path, code):
    result["seconds"] = round(time.time() - START_TS, 2)
    result["partial"] = bool(result["applied"] and result["errors"])
    emit(result, result_path)
    # exit code 1 only when nothing at all was applied
    sys.exit(code if (code != 0 or not result["applied"]) else 0)


if __name__ == "__main__":
    main()
